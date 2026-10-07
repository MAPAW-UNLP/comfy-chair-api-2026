"""Infraestructura de tests del rol Revisor (issue 0.1-BE).

Todos los tests del rol heredan de ReviewerTestCase para no repetir el armado
de datos, el token JWT ni la carpeta temporal de archivos.
"""
import itertools
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone as dj_timezone
from rest_framework.test import APIClient

from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from conference_session.models import Session
from reviewer.models import Review
from user.models import User

PASSWORD_DE_PRUEBA = "clave-de-prueba-123"


class ReviewerTestCase(TestCase):
    """Clase base: datos de prueba, token JWT y media temporal."""

    # Contador compartido: evita títulos y emails repetidos.
    # Los títulos de conferencia y de sesión son únicos (sin distinguir
    # mayúsculas), así que cada objeto creado lleva un número distinto.
    _contador = itertools.count(1)

    def setUp(self):
        super().setUp()
        # Media temporal: los archivos que suben los tests van a una carpeta
        # nueva y se borra al terminar, así no se ensucia media/.
        self._media_dir = tempfile.mkdtemp(prefix="comfychair-tests-")
        sobrescritura = override_settings(MEDIA_ROOT=self._media_dir)
        sobrescritura.enable()
        self.addCleanup(sobrescritura.disable)
        self.addCleanup(shutil.rmtree, self._media_dir, ignore_errors=True)
        # Cliente sin token (para probar el 401).
        self.client = APIClient()

    def _siguiente(self):
        return next(self._contador)

    # ---------- Datos de prueba ----------

    def crear_usuario(self, role="user", **extra):
        """Crea un usuario. No usa create_user() porque el modelo no tiene
        username y el manager por defecto lo pide."""
        n = self._siguiente()
        datos = {
            "email": f"usuario{n}@test.com",
            "full_name": f"Usuario {n}",
            "affiliation": "UNLP",
            "role": role,
        }
        datos.update(extra)
        usuario = User(**datos)
        usuario.set_password(PASSWORD_DE_PRUEBA)
        usuario.save()
        return usuario

    def crear_conferencia(self, **extra):
        n = self._siguiente()
        hoy = date.today()
        datos = {
            "title": f"Conferencia {n}",
            "description": "Conferencia de prueba",
            "start_date": hoy - timedelta(days=30),
            "end_date": hoy + timedelta(days=90),
        }
        datos.update(extra)
        return Conference.objects.create(**datos)

    def crear_sesion(self, conferencia=None, **extra):
        """Session.save() valida que el deadline caiga entre el inicio y el fin
        de la conferencia, por eso el deadline por defecto es el fin."""
        conferencia = conferencia or self.crear_conferencia()
        n = self._siguiente()
        datos = {
            "title": f"Sesión {n}",
            "deadline": conferencia.end_date,
            "capacity": 10,
            "conference": conferencia,
        }
        datos.update(extra)
        return Session.objects.create(**datos)

    def crear_articulo(self, autor=None, sesion=None, con_sesion=True, **extra):
        """Crea un artículo con un PDF falso. Si no se pasa sesión, crea una
        (con_sesion=False deja el artículo sin sesión)."""
        autor = autor or self.crear_usuario()
        if sesion is None and con_sesion:
            sesion = self.crear_sesion()
        n = self._siguiente()
        datos = {
            "title": f"Artículo {n}",
            "type": "regular",
            "abstract": "Resumen de prueba",
            "main_file": SimpleUploadedFile(
                "articulo.pdf", b"%PDF-1.4 prueba", content_type="application/pdf"
            ),
            "corresponding_author": autor,
            "session": sesion,
        }
        datos.update(extra)
        articulo = Article.objects.create(**datos)
        articulo.authors.add(autor)  # relación muchos a muchos: va después
        return articulo

    def crear_asignacion(self, revisor, articulo, **extra):
        return ReviewAssignment.objects.create(
            reviewer=revisor, article=articulo, **extra
        )

    def crear_review(self, revisor, articulo, publicada=False, **extra):
        datos = {
            "reviewer": revisor,
            "article": articulo,
            "score": 1,
            "opinion": "Opinión de prueba",
            "is_published": publicada,
            "created_at": dj_timezone.now() if publicada else None,
        }
        datos.update(extra)
        return Review.objects.create(**datos)

    # ---------- Autenticación ----------

    def token_para(self, usuario, expira_en=3600):
        """Genera un JWT igual al que arma LoginAPI. expira_en en segundos;
        un valor negativo devuelve un token ya vencido."""
        ahora = datetime.now(timezone.utc)
        payload = {
            "user_id": usuario.id,
            "rol": usuario.role,
            "iat": ahora,
            "exp": ahora + timedelta(seconds=expira_en),
        }
        return jwt.encode(
            payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

    def cliente_de(self, usuario, expira_en=3600):
        """Devuelve un cliente nuevo que llama a la API como ese usuario.
        Sirve para tests con dos usuarios a la vez."""
        cliente = APIClient()
        token = self.token_para(usuario, expira_en=expira_en)
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return cliente