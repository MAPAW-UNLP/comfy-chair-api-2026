import itertools
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from article.models import Article, ArticleHistory, Source
from conference.models import Conference
from conference_session.models import Session
from notification.models import Notification
from user.models import User

PASSWORD_DE_PRUEBA = "clave-de-prueba-123"


class ArticleDraftTestCase(TestCase):
    """Infraestructura compartida por los tests de borradores (draft -> reception)."""

    _contador = itertools.count(1)

    def setUp(self):
        super().setUp()
        self._media_dir = tempfile.mkdtemp(prefix="comfychair-tests-")
        sobrescritura = override_settings(MEDIA_ROOT=self._media_dir)
        sobrescritura.enable()
        self.addCleanup(sobrescritura.disable)
        self.addCleanup(shutil.rmtree, self._media_dir, ignore_errors=True)
        self.client = APIClient()

    def _siguiente(self):
        return next(self._contador)

    def crear_usuario(self, **extra):
        n = self._siguiente()
        datos = {
            "email": f"usuario{n}@test.com",
            "full_name": f"Usuario {n}",
            "affiliation": "UNLP",
            "role": "user",
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

    def crear_borrador(self, autor=None, sesion=None, **extra):
        """Crea un Article en draft solo con los mínimos (title + session)."""
        autor = autor or self.crear_usuario()
        sesion = sesion or self.crear_sesion()
        n = self._siguiente()
        datos = {
            "title": f"Borrador {n}",
            "status": "draft",
            "session": sesion,
        }
        datos.update(extra)
        articulo = Article.objects.create(**datos)
        articulo.authors.add(autor)
        return articulo

    def completar_borrador(self, articulo, autor, tipo="regular"):
        """Completa los campos obligatorios de un borrador para que pueda enviarse."""
        articulo.abstract = "Resumen de prueba"
        articulo.type = tipo
        articulo.main_file = SimpleUploadedFile(
            "articulo.pdf", b"%PDF-1.4 prueba", content_type="application/pdf"
        )
        articulo.corresponding_author = autor
        articulo.save()
        if tipo == "poster":
            Source.objects.create(
                article=articulo,
                file_path=SimpleUploadedFile("fuente.zip", b"contenido"),
                filename="fuente.zip",
            )
        return articulo

    def token_para(self, usuario, expira_en=3600):
        ahora = datetime.now(timezone.utc)
        payload = {
            "user_id": usuario.id,
            "rol": usuario.role,
            "iat": ahora,
            "exp": ahora + timedelta(seconds=expira_en),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def cliente_de(self, usuario, expira_en=3600):
        cliente = APIClient()
        token = self.token_para(usuario, expira_en=expira_en)
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return cliente


class CrearBorradorTests(ArticleDraftTestCase):
    def test_crear_borrador_minimo_no_exige_completitud(self):
        autor = self.crear_usuario()
        sesion = self.crear_sesion()
        respuesta = self.cliente_de(autor).post(
            "/api/article/",
            {
                "status": "draft",
                "title": "Mi borrador",
                "session_id": sesion.id,
                "authors_ids": [autor.id],
            },
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(respuesta.data["status"], "draft")

        articulo = Article.objects.get(pk=respuesta.data["id"])
        self.assertFalse(
            Notification.objects.filter(article=articulo, title="Artículo enviado").exists()
        )
        self.assertTrue(
            ArticleHistory.objects.filter(article=articulo, event_type="draft_created").exists()
        )

    def test_crear_borrador_sin_titulo_o_sesion_falla(self):
        autor = self.crear_usuario()
        respuesta = self.cliente_de(autor).post(
            "/api/article/", {"status": "draft"}, format="multipart"
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("title", respuesta.data["errors"])
        self.assertIn("session_id", respuesta.data["errors"])

    def test_crear_directamente_como_reception_exige_completitud(self):
        autor = self.crear_usuario()
        sesion = self.crear_sesion()
        respuesta = self.cliente_de(autor).post(
            "/api/article/",
            {"status": "reception", "title": "Completo", "session_id": sesion.id},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, 400)
        for campo in ("abstract", "main_file", "type", "authors_ids", "corresponding_author_id"):
            self.assertIn(campo, respuesta.data["errors"])


class EditarBorradorTests(ArticleDraftTestCase):
    def test_patch_no_cambia_el_estado_aunque_se_envie_status_reception(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        respuesta = self.cliente_de(autor).patch(
            f"/api/article/{borrador.id}/",
            {"status": "reception", "title": "Nuevo título"},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        borrador.refresh_from_db()
        self.assertEqual(borrador.status, "draft")
        self.assertEqual(borrador.title, "Nuevo título")

    def test_editar_borrador_no_notifica(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        Notification.objects.filter(article=borrador).delete()

        respuesta = self.cliente_de(autor).patch(
            f"/api/article/{borrador.id}/", {"title": "Otro título"}, format="multipart"
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertFalse(Notification.objects.filter(article=borrador).exists())


class SubmitTests(ArticleDraftTestCase):
    def test_submit_borrador_incompleto_devuelve_400_con_errores_por_campo(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("abstract", respuesta.data)
        self.assertIn("main_file", respuesta.data)

    def test_submit_borrador_completo_pasa_a_reception_y_notifica(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        self.completar_borrador(borrador, autor, tipo="regular")

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data["status"], "reception")

        borrador.refresh_from_db()
        self.assertEqual(borrador.status, "reception")
        self.assertTrue(
            ArticleHistory.objects.filter(article=borrador, event_type="submitted").exists()
        )
        self.assertTrue(
            Notification.objects.filter(article=borrador, title="Artículo enviado").exists()
        )

    def test_submit_poster_sin_source_falla(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        self.completar_borrador(borrador, autor, tipo="poster")
        Source.objects.filter(article=borrador).delete()

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("source_file", respuesta.data)

    def test_agregar_source_por_patch_permite_submit_de_poster(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        self.completar_borrador(borrador, autor, tipo="poster")
        Source.objects.filter(article=borrador).delete()

        respuesta = self.cliente_de(autor).patch(
            f"/api/article/{borrador.id}/",
            {"sources": SimpleUploadedFile("fuente.zip", b"contenido")},
            format="multipart",
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertTrue(Source.objects.filter(article=borrador).exists())

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)

    def test_submit_con_deadline_vencido_falla(self):
        autor = self.crear_usuario()
        conferencia = self.crear_conferencia(
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=1),
        )
        sesion = self.crear_sesion(conferencia, deadline=conferencia.end_date)
        borrador = self.crear_borrador(autor=autor, sesion=sesion)
        self.completar_borrador(borrador, autor, tipo="regular")

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 400)

    def test_submit_sobre_articulo_que_no_esta_en_draft_devuelve_409(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        self.completar_borrador(borrador, autor, tipo="regular")
        borrador.status = "reception"
        borrador.save()

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 409)


class PermisosBorradorTests(ArticleDraftTestCase):
    def test_get_de_borrador_ajeno_devuelve_404(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)

        respuesta = self.cliente_de(otro).get(f"/api/article/{borrador.id}/")
        self.assertEqual(respuesta.status_code, 404)

    def test_patch_de_borrador_ajeno_devuelve_404(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)

        respuesta = self.cliente_de(otro).patch(
            f"/api/article/{borrador.id}/", {"title": "Hackeo"}, format="multipart"
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_submit_de_borrador_ajeno_devuelve_404(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)

        respuesta = self.cliente_de(otro).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 404)

    def test_get_de_borrador_propio_funciona(self):
        autor = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)

        respuesta = self.cliente_de(autor).get(f"/api/article/{borrador.id}/")
        self.assertEqual(respuesta.status_code, 200)

    def test_get_de_articulo_que_ya_no_es_draft_lo_puede_ver_cualquiera(self):
        # Antes de este feature, cualquier autenticado podía ver el detalle de
        # un artículo ya enviado (chairs, revisores vía GET /api/article/{id}/).
        # IsArticleAuthor solo debe restringir mientras el artículo es draft.
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        articulo = self.crear_borrador(autor=autor)
        self.completar_borrador(articulo, autor)
        articulo.status = "reception"
        articulo.save()

        respuesta = self.cliente_de(otro).get(f"/api/article/{articulo.id}/")
        self.assertEqual(respuesta.status_code, 200)


class ListadosMuestranBorradorPropioTests(ArticleDraftTestCase):
    """Un borrador se ve mezclado con el resto de los artículos en los
    listados, pero solo para su propio autor; para cualquier otro usuario
    sigue sin aparecer."""

    def test_getArticlesBySessionId_incluye_el_borrador_propio_pero_no_el_ajeno(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        sesion = self.crear_sesion()
        borrador = self.crear_borrador(autor=autor, sesion=sesion)
        completo = self.crear_borrador(autor=autor, sesion=sesion, status="reception")
        self.completar_borrador(completo, autor)

        respuesta_autor = self.cliente_de(autor).get(f"/api/article/getArticlesBySessionId/{sesion.id}/")
        ids_autor = [a["id"] for a in respuesta_autor.data]
        self.assertIn(completo.id, ids_autor)
        self.assertIn(borrador.id, ids_autor)

        respuesta_otro = self.cliente_de(otro).get(f"/api/article/getArticlesBySessionId/{sesion.id}/")
        ids_otro = [a["id"] for a in respuesta_otro.data]
        self.assertIn(completo.id, ids_otro)
        self.assertNotIn(borrador.id, ids_otro)

    def test_getArticlesByConferenceId_incluye_el_borrador_propio_pero_no_el_ajeno(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        conferencia = self.crear_conferencia()
        sesion = self.crear_sesion(conferencia)
        borrador = self.crear_borrador(autor=autor, sesion=sesion)
        completo = self.crear_borrador(autor=autor, sesion=sesion, status="reception")
        self.completar_borrador(completo, autor)

        respuesta_autor = self.cliente_de(autor).get(
            f"/api/article/getArticlesByConferenceId/{conferencia.id}/"
        )
        ids_autor = [a["id"] for a in respuesta_autor.data]
        self.assertIn(completo.id, ids_autor)
        self.assertIn(borrador.id, ids_autor)

        respuesta_otro = self.cliente_de(otro).get(
            f"/api/article/getArticlesByConferenceId/{conferencia.id}/"
        )
        ids_otro = [a["id"] for a in respuesta_otro.data]
        self.assertNotIn(borrador.id, ids_otro)

    def test_listado_general_incluye_el_borrador_propio_pero_no_el_ajeno(self):
        autor = self.crear_usuario()
        otro = self.crear_usuario()
        borrador = self.crear_borrador(autor=autor)
        completo = self.crear_borrador(autor=autor, status="reception")
        self.completar_borrador(completo, autor)

        respuesta_autor = self.cliente_de(autor).get("/api/article/")
        ids_autor = [a["id"] for a in respuesta_autor.data]
        self.assertIn(completo.id, ids_autor)
        self.assertIn(borrador.id, ids_autor)

        respuesta_otro = self.cliente_de(otro).get("/api/article/")
        ids_otro = [a["id"] for a in respuesta_otro.data]
        self.assertNotIn(borrador.id, ids_otro)


class UserFullDataConBorradoresTests(ArticleDraftTestCase):
    def test_user_full_data_no_rompe_con_un_borrador(self):
        autor = self.crear_usuario()
        self.crear_borrador(autor=autor)

        respuesta = self.cliente_de(autor).get("/user/user-full-data/")
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
