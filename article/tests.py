import itertools
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from article.models import Article, ArticleFile, ArticleHistory, Source
from conference.models import Conference
from conference_session.models import Session, get_submission_period
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
        # Los tests existentes de escritura usan una conferencia con período
        # de submissions configurado explícitamente.
        datos.setdefault("submission_start", datos["start_date"])
        datos.setdefault("submission_end", datos["end_date"])
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

    def test_submit_con_periodo_de_conferencia_vencido_falla(self):
        autor = self.crear_usuario()
        conferencia = self.crear_conferencia(
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=1),
        )
        sesion = self.crear_sesion(conferencia, deadline=conferencia.end_date)
        borrador = self.crear_borrador(autor=autor, sesion=sesion)
        self.completar_borrador(borrador, autor, tipo="regular")

        respuesta = self.cliente_de(autor).post(f"/api/article/{borrador.id}/submit/")
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.data["code"], "submission_period_closed")

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


UTC = timezone.utc
OPEN_NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


class SubmissionPeriodTests(ArticleDraftTestCase):
    def setUp(self):
        super().setUp()
        self.clock_patch = patch(
            'conference_session.models.timezone.now',
            return_value=OPEN_NOW,
        )
        self.clock = self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.author = self.crear_usuario()
        self.conference = self.crear_conferencia(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 31),
            submission_start=date(2026, 11, 10),
            submission_end=date(2026, 11, 30),
        )
        self.session = self.crear_sesion(
            self.conference, deadline=date(2026, 11, 30)
        )
        self.client = self.cliente_de(self.author)

    def _draft_payload(self, session=None):
        return {
            'status': 'draft',
            'title': 'Mi borrador',
            'session_id': (session or self.session).pk,
            'authors_ids': [self.author.pk],
        }

    def _complete_payload(self, session=None):
        return {
            'status': 'reception',
            'title': 'Artículo completo',
            'session_id': (session or self.session).pk,
            'abstract': 'Resumen completo',
            'type': 'regular',
            'authors_ids': [self.author.pk],
            'corresponding_author_id': self.author.pk,
            'main_file': SimpleUploadedFile(
                'article.pdf', b'%PDF-1.4 test', content_type='application/pdf'
            ),
        }

    def _closed(self, response):
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data, {
            'code': 'submission_period_closed',
            'detail': 'El período de submissions de esta sesión está cerrado.',
        })

    def test_boundaries_are_half_open_and_include_full_submission_end_day(self):
        period = get_submission_period(self.session)
        self.assertEqual(period.start, datetime(2026, 11, 10, tzinfo=UTC))
        self.assertEqual(period.end_exclusive, datetime(2026, 12, 1, tzinfo=UTC))
        for now, expected in (
            (datetime(2026, 11, 9, 23, 59, 59, tzinfo=UTC), False),
            (datetime(2026, 11, 10, 0, 0, tzinfo=UTC), True),
            (OPEN_NOW, True),
            (datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC), True),
            (datetime(2026, 12, 1, 0, 0, tzinfo=UTC), False),
            (datetime(2026, 12, 2, 0, 0, tzinfo=UTC), False),
        ):
            with self.subTest(now=now):
                self.assertEqual(period.is_open(now), expected)

    def test_missing_boundaries_close_period_without_fallback(self):
        original_start = self.conference.submission_start
        original_end = self.conference.submission_end
        for start, end in (
            (None, original_end),
            (original_start, None),
            (None, None),
        ):
            with self.subTest(start=start, end=end):
                self.conference.submission_start = start
                self.conference.submission_end = end
                self.conference.save()
                period = get_submission_period(self.session)
                self.assertEqual(period.start is None, start is None)
                self.assertEqual(period.end_exclusive is None, end is None)
                self.assertFalse(period.is_open())
                response = self.client.get(
                    f'/api/article/submission-status/?session_id={self.session.pk}'
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['submission_start'],
                                 '2026-11-10T00:00:00Z' if start else None)
                self.assertEqual(response.data['submission_deadline'],
                                 '2026-12-01T00:00:00Z' if end else None)
                self.assertFalse(response.data['submission_open'])
                self._closed(self.client.post(
                    '/api/article/', self._draft_payload(), format='multipart'
                ))

    def test_start_after_end_is_closed_without_server_error(self):
        self.conference.submission_start = date(2026, 12, 2)
        self.conference.save()
        period = get_submission_period(self.session)
        self.assertFalse(period.is_open())
        response = self.client.get(
            f'/api/article/submission-status/?session_id={self.session.pk}'
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['submission_open'])
        self._closed(self.client.post(
            '/api/article/', self._draft_payload(), format='multipart'
        ))

    def test_future_datetime_fields_keep_time_and_timezone(self):
        eastern = timezone(timedelta(hours=-3))
        session = SimpleNamespace(
            conference=SimpleNamespace(
                submission_start=datetime(2026, 11, 10, 9, 15, tzinfo=eastern),
                submission_end=datetime(2026, 11, 30, 18, 30, tzinfo=eastern),
            ),
        )
        period = get_submission_period(session)
        self.assertEqual(period.start, datetime(2026, 11, 10, 12, 15, tzinfo=UTC))
        self.assertEqual(
            period.end_exclusive, datetime(2026, 11, 30, 21, 30, tzinfo=UTC)
        )

    def test_create_draft_and_complete_article_when_open(self):
        draft = self.client.post('/api/article/', self._draft_payload(), format='multipart')
        self.assertEqual(draft.status_code, 201, draft.data)
        self.assertEqual(draft.data['status'], 'draft')

        complete = self.client.post(
            '/api/article/', self._complete_payload(), format='multipart'
        )
        self.assertEqual(complete.status_code, 201, complete.data)
        self.assertEqual(complete.data['status'], 'reception')

    def test_create_draft_and_complete_article_rejected_before_start(self):
        self.clock.return_value = datetime(2026, 11, 9, 23, tzinfo=UTC)
        self._assert_create_rejected_without_side_effects()

    def test_create_draft_and_complete_article_rejected_after_submission_end(self):
        self.clock.return_value = datetime(2026, 12, 1, tzinfo=UTC)
        self._assert_create_rejected_without_side_effects()

    def _assert_create_rejected_without_side_effects(self):
        for payload in (self._draft_payload(), self._complete_payload()):
            with self.subTest(status=payload['status']):
                response = self.client.post('/api/article/', payload, format='multipart')
                self._closed(response)
        self.assertEqual(Article.objects.count(), 0)
        self.assertEqual(Source.objects.count(), 0)
        self.assertEqual(ArticleHistory.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_put_and_patch_allowed_while_open(self):
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        response = self.client.put(
            f'/api/article/{article.pk}/',
            {
                'title': 'Título PUT',
                'session_id': self.session.pk,
                'authors_ids': [self.author.pk],
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.data)
        article.refresh_from_db()
        self.assertTrue(article.authors.filter(pk=self.author.pk).exists())
        response = self.client.patch(
            f'/api/article/{article.pk}/', {'title': 'Título PATCH'},
            format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.data)
        article.refresh_from_db()
        self.assertEqual(article.title, 'Título PATCH')

    def test_put_and_patch_rejected_before_start_and_after_submission_end(self):
        for now in (
            datetime(2026, 11, 9, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ):
            self.clock.return_value = now
            for method in ('put', 'patch'):
                with self.subTest(now=now, method=method):
                    article = self.crear_borrador(
                        autor=self.author, sesion=self.session
                    )
                    before_history = ArticleHistory.objects.count()
                    before_notifications = Notification.objects.count()
                    payload = {
                        'title': 'No guardar',
                        'sources': SimpleUploadedFile('source.zip', b'contenido'),
                    }
                    if method == 'put':
                        payload['session_id'] = self.session.pk
                    response = getattr(self.client, method)(
                        f'/api/article/{article.pk}/', payload, format='multipart'
                    )
                    self._closed(response)
                    article.refresh_from_db()
                    self.assertNotEqual(article.title, 'No guardar')
                    self.assertEqual(Source.objects.filter(article=article).count(), 0)
                    self.assertEqual(ArticleHistory.objects.count(), before_history)
                    self.assertEqual(Notification.objects.count(), before_notifications)

    def test_session_change_allowed_only_when_both_periods_open(self):
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        other_conference = self.crear_conferencia(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 31),
            submission_start=date(2026, 11, 10),
            submission_end=date(2026, 11, 30),
        )
        destination = self.crear_sesion(
            other_conference, deadline=date(2026, 11, 30)
        )
        response = self.client.patch(
            f'/api/article/{article.pk}/',
            {'session_id': destination.pk}, format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.data)
        article.refresh_from_db()
        self.assertEqual(article.session_id, destination.pk)

    def test_session_change_rejected_when_origin_is_closed(self):
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        self.conference.submission_end = date(2026, 11, 15)
        self.conference.save()
        other_conference = self.crear_conferencia(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 31),
            submission_start=date(2026, 11, 10),
            submission_end=date(2026, 11, 30),
        )
        destination = self.crear_sesion(
            other_conference, deadline=date(2026, 11, 30)
        )
        response = self.client.patch(
            f'/api/article/{article.pk}/',
            {'session_id': destination.pk}, format='multipart',
        )
        self._closed(response)
        article.refresh_from_db()
        self.assertEqual(article.session_id, self.session.pk)

    def test_session_change_rejected_when_destination_is_closed(self):
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        other_conference = self.crear_conferencia(
            start_date=date(2026, 11, 1),
            end_date=date(2026, 12, 31),
            submission_start=date(2026, 11, 10),
            submission_end=date(2026, 11, 15),
        )
        destination = self.crear_sesion(
            other_conference, deadline=date(2026, 11, 30)
        )
        response = self.client.put(
            f'/api/article/{article.pk}/',
            {'title': 'Nuevo', 'session_id': destination.pk},
            format='multipart',
        )
        self._closed(response)
        article.refresh_from_db()
        self.assertEqual(article.session_id, self.session.pk)
        self.assertNotEqual(article.title, 'Nuevo')

    def test_sessions_in_same_conference_share_period_and_cannot_bypass_close(self):
        other_session = self.crear_sesion(
            self.conference, deadline=date(2026, 11, 15)
        )
        first = self.client.get(
            f'/api/article/submission-status/?session_id={self.session.pk}'
        )
        second = self.client.get(
            f'/api/article/submission-status/?session_id={other_session.pk}'
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(first.data['submission_open'])
        for key in ('submission_start', 'submission_deadline', 'submission_open'):
            self.assertEqual(first.data[key], second.data[key])

        article = self.crear_borrador(autor=self.author, sesion=self.session)
        self.clock.return_value = datetime(2026, 12, 1, tzinfo=UTC)
        response = self.client.patch(
            f'/api/article/{article.pk}/',
            {'session_id': other_session.pk}, format='multipart',
        )
        self._closed(response)
        article.refresh_from_db()
        self.assertEqual(article.session_id, self.session.pk)

    def test_expired_session_deadline_does_not_close_conference_period(self):
        self.session.deadline = date(2026, 11, 15)
        self.session.save()
        self.assertTrue(get_submission_period(self.session).is_open())

        draft = self.client.post(
            '/api/article/', self._draft_payload(), format='multipart'
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        direct = self.client.post(
            '/api/article/', self._complete_payload(), format='multipart'
        )
        self.assertEqual(direct.status_code, 201, direct.data)

        article = Article.objects.get(pk=draft.data['id'])
        updated = self.client.put(
            f'/api/article/{article.pk}/',
            {
                'title': 'Título PUT',
                'session_id': self.session.pk,
                'authors_ids': [self.author.pk],
            },
            format='multipart',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        updated = self.client.patch(
            f'/api/article/{article.pk}/',
            {'title': 'Título PATCH'}, format='multipart',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.completar_borrador(article, self.author)
        submitted = self.client.post(f'/api/article/{article.pk}/submit/')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'reception')

    def test_future_session_deadline_does_not_extend_closed_conference_period(self):
        self.conference.submission_end = date(2026, 11, 15)
        self.conference.save()
        self.assertEqual(self.session.deadline, date(2026, 11, 30))
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        self.completar_borrador(article, self.author)
        before_history = ArticleHistory.objects.count()
        before_notifications = Notification.objects.count()
        before_articles = Article.objects.count()

        for payload in (self._draft_payload(), self._complete_payload()):
            with self.subTest(status=payload['status']):
                self._closed(self.client.post(
                    '/api/article/', payload, format='multipart'
                ))
        for method, payload in (
            ('put', {'title': 'No guardar', 'session_id': self.session.pk}),
            ('patch', {'title': 'No guardar'}),
        ):
            with self.subTest(method=method):
                self._closed(getattr(self.client, method)(
                    f'/api/article/{article.pk}/', payload, format='multipart'
                ))
        self._closed(self.client.post(f'/api/article/{article.pk}/submit/'))
        article.refresh_from_db()
        self.assertEqual(article.status, 'draft')
        self.assertNotEqual(article.title, 'No guardar')
        self.assertEqual(Article.objects.count(), before_articles)
        self.assertEqual(ArticleHistory.objects.count(), before_history)
        self.assertEqual(Notification.objects.count(), before_notifications)

    def test_submit_allowed_while_open_and_rejected_outside_period(self):
        for now, allowed in (
            (OPEN_NOW, True),
            (datetime(2026, 11, 9, tzinfo=UTC), False),
            (datetime(2026, 12, 1, tzinfo=UTC), False),
        ):
            with self.subTest(now=now):
                article = self.crear_borrador(autor=self.author, sesion=self.session)
                self.completar_borrador(article, self.author)
                before_history = ArticleHistory.objects.count()
                before_notifications = Notification.objects.count()
                self.clock.return_value = now
                response = self.client.post(f'/api/article/{article.pk}/submit/')
                article.refresh_from_db()
                if allowed:
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(article.status, 'reception')
                else:
                    self._closed(response)
                    self.assertEqual(article.status, 'draft')
                    self.assertEqual(ArticleHistory.objects.count(), before_history)
                    self.assertEqual(Notification.objects.count(), before_notifications)

    def test_submission_status_reports_open_and_closed_with_exclusive_end(self):
        url = f'/api/article/submission-status/?session_id={self.session.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {
            'session_id': self.session.pk,
            'submission_open': True,
            'submission_start': '2026-11-10T00:00:00Z',
            'submission_deadline': '2026-12-01T00:00:00Z',
        })
        self.clock.return_value = datetime(2026, 12, 1, tzinfo=UTC)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['submission_open'])

    def test_submission_status_validates_session_id_and_authentication(self):
        for query in (
            '', '?session_id=', '?session_id=abc', '?session_id=0',
            '?session_id=999999999999999999999999999999',
        ):
            with self.subTest(query=query):
                response = self.client.get('/api/article/submission-status/' + query)
                self.assertEqual(response.status_code, 400, response.data)
        response = self.client.get(
            '/api/article/submission-status/?session_id=999999999'
        )
        self.assertEqual(response.status_code, 404)
        anonymous = APIClient().get(
            f'/api/article/submission-status/?session_id={self.session.pk}'
        )
        self.assertEqual(anonymous.status_code, 401)

    def test_closed_draft_remains_readable_and_other_authors_stay_blocked(self):
        article = self.crear_borrador(autor=self.author, sesion=self.session)
        stranger = self.crear_usuario()
        self.clock.return_value = datetime(2026, 12, 1, tzinfo=UTC)
        self.assertEqual(
            self.client.get(f'/api/article/{article.pk}/').status_code, 200
        )
        self.assertEqual(
            self.cliente_de(stranger).patch(
                f'/api/article/{article.pk}/', {'title': 'Ajeno'},
                format='multipart',
            ).status_code,
            404,
        )

    def test_sessionless_legacy_article_returns_400_on_write(self):
        article = Article.objects.create(title='Heredado', status='draft')
        article.authors.add(self.author)
        self.assertEqual(
            self.client.get(f'/api/article/{article.pk}/').status_code, 200
        )
        update = self.client.patch(
            f'/api/article/{article.pk}/', {'title': 'Nuevo'}, format='multipart'
        )
        self.assertEqual(update.status_code, 400, update.data)
        submit = self.client.post(f'/api/article/{article.pk}/submit/')
        self.assertEqual(submit.status_code, 400, submit.data)
        article.refresh_from_db()
        self.assertEqual(article.title, 'Heredado')
        self.assertEqual(article.status, 'draft')

    def test_invalid_session_id_still_returns_serializer_validation_error(self):
        response = self.client.post(
            '/api/article/',
            {'title': 'Borrador', 'status': 'draft', 'session_id': 999999999},
            format='multipart',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('session_id', response.data['errors'])
