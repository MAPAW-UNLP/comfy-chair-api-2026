"""Tests de GET /api/reviewer/assignments/ (issue 3.1-BE)."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .base import ReviewerTestCase

TEXTO_SECRETO = "TEXTO-SECRETO-QUE-NO-DEBE-SALIR-123"


class ReviewerAssignmentsTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.revisor = self.crear_usuario()
        self.cliente = self.cliente_de(self.revisor)
        self.url = reverse("reviewer-assignments")

    # ---------- utilidades ----------

    def asignar(self, sesion=None, revisor=None):
        """Crea un artículo (en la sesión dada, o sin sesión) y se lo asigna."""
        articulo = self.crear_articulo(sesion=sesion, con_sesion=False)
        self.crear_asignacion(revisor or self.revisor, articulo)
        return articulo

    def pedir(self, **params):
        return self.cliente.get(self.url, params)

    def ids_de_articulos(self, respuesta):
        return [r["article"]["id"] for r in respuesta.json()["results"]]

    # ---------- acceso ----------

    def test_sin_token_responde_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    # ---------- lista y filtros ----------

    def test_sin_asignaciones_responde_200_vacio(self):
        respuesta = self.pedir()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["results"], [])
        self.assertEqual(respuesta.json()["stats"], {
            "total": 0, "published": 0, "draft": 0, "pending": 0, "by_session": [],
        })

    def test_sin_filtro_devuelve_todas_las_conferencias(self):
        a = self.asignar(self.crear_sesion())
        b = self.asignar(self.crear_sesion())
        self.assertCountEqual(self.ids_de_articulos(self.pedir()), [a.id, b.id])

    def test_filtra_por_conferencia(self):
        sesion_a = self.crear_sesion()
        sesion_b = self.crear_sesion()
        articulo_a = self.asignar(sesion_a)
        self.asignar(sesion_b)
        respuesta = self.pedir(conference_id=sesion_a.conference_id)
        self.assertEqual(self.ids_de_articulos(respuesta), [articulo_a.id])

    def test_filtra_por_sesion(self):
        conferencia = self.crear_conferencia()
        sesion_1 = self.crear_sesion(conferencia)
        sesion_2 = self.crear_sesion(conferencia)
        articulo_1 = self.asignar(sesion_1)
        self.asignar(sesion_2)
        respuesta = self.pedir(session_id=sesion_1.id)
        self.assertEqual(self.ids_de_articulos(respuesta), [articulo_1.id])

    def test_los_filtros_se_combinan_con_and(self):
        sesion = self.crear_sesion()
        otra_conferencia = self.crear_conferencia()
        self.asignar(sesion)
        respuesta = self.pedir(conference_id=otra_conferencia.id, session_id=sesion.id)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["results"], [])

    def test_no_aparecen_asignaciones_de_otro_revisor(self):
        sesion = self.crear_sesion()
        propio = self.asignar(sesion)
        self.asignar(sesion, revisor=self.crear_usuario())
        self.assertEqual(self.ids_de_articulos(self.pedir()), [propio.id])

    def test_no_aparecen_asignaciones_borradas(self):
        sesion = self.crear_sesion()
        visible = self.asignar(sesion)
        borrado = self.crear_articulo(sesion=sesion)
        self.crear_asignacion(self.revisor, borrado, deleted=True)
        self.assertEqual(self.ids_de_articulos(self.pedir()), [visible.id])

    # ---------- estados ----------

    def test_estados_pending_draft_y_published(self):
        sesion = self.crear_sesion()
        sin_review = self.asignar(sesion)
        con_borrador = self.asignar(sesion)
        publicada = self.asignar(sesion)
        borrador = self.crear_review(self.revisor, con_borrador)
        review_publicada = self.crear_review(self.revisor, publicada, publicada=True)

        por_articulo = {r["article"]["id"]: r for r in self.pedir().json()["results"]}

        self.assertEqual(por_articulo[sin_review.id]["review_status"], "pending")
        self.assertIsNone(por_articulo[sin_review.id]["review_id"])
        self.assertEqual(por_articulo[con_borrador.id]["review_status"], "draft")
        self.assertEqual(por_articulo[con_borrador.id]["review_id"], borrador.id)
        self.assertEqual(por_articulo[publicada.id]["review_status"], "published")
        self.assertEqual(por_articulo[publicada.id]["review_id"], review_publicada.id)

    def test_la_review_de_otro_revisor_no_cambia_mi_estado(self):
        articulo = self.asignar(self.crear_sesion())
        otro = self.crear_usuario()
        self.crear_asignacion(otro, articulo)
        self.crear_review(otro, articulo, publicada=True)

        item = self.pedir().json()["results"][0]
        self.assertEqual(item["review_status"], "pending")
        self.assertIsNone(item["review_id"])

    # ---------- estadísticas ----------

    def test_stats_y_progreso_por_sesion(self):
        conferencia = self.crear_conferencia()
        sesion_1 = self.crear_sesion(conferencia)
        sesion_2 = self.crear_sesion(conferencia)
        publicada = self.asignar(sesion_1)
        borrador = self.asignar(sesion_1)
        self.asignar(sesion_2)
        self.crear_review(self.revisor, publicada, publicada=True)
        self.crear_review(self.revisor, borrador)

        stats = self.pedir().json()["stats"]

        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["published"], 1)
        self.assertEqual(stats["draft"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["by_session"], [
            {"session_id": sesion_1.id, "title": sesion_1.title, "total": 2, "published": 1},
            {"session_id": sesion_2.id, "title": sesion_2.title, "total": 1, "published": 0},
        ])

    def test_articulo_sin_sesion_cuenta_en_el_total_pero_no_en_by_session(self):
        articulo = self.asignar(sesion=None)
        respuesta = self.pedir().json()

        item = respuesta["results"][0]
        self.assertEqual(item["article"]["id"], articulo.id)
        self.assertIsNone(item["session"])
        self.assertIsNone(item["conference"])
        self.assertTrue(item["review_period"]["is_open"])
        self.assertEqual(respuesta["stats"]["total"], 1)
        self.assertEqual(respuesta["stats"]["by_session"], [])

    # ---------- validación de parámetros ----------

    def test_conference_id_no_numerico_responde_400(self):
        respuesta = self.pedir(conference_id="abc")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("error", respuesta.json())

    def test_session_id_no_numerico_responde_400(self):
        respuesta = self.pedir(session_id="abc")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("error", respuesta.json())

    # ---------- rendimiento ----------

    def contar_consultas(self):
        with CaptureQueriesContext(connection) as consultas:
            respuesta = self.cliente.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        return len(consultas)

    def test_la_cantidad_de_consultas_no_depende_de_la_cantidad_de_asignaciones(self):
        sesion = self.crear_sesion()
        self.asignar(sesion)
        con_una = self.contar_consultas()
        for _ in range(19):
            self.asignar(sesion)
        con_veinte = self.contar_consultas()
        self.assertEqual(con_una, con_veinte)

    # ---------- privacidad ----------

    def test_no_expone_el_contenido_de_las_reviews(self):
        articulo = self.asignar(self.crear_sesion())
        self.crear_review(
            self.revisor, articulo, publicada=True, opinion=TEXTO_SECRETO, score=3
        )

        respuesta = self.pedir()

        self.assertNotIn(TEXTO_SECRETO.encode(), respuesta.content)
        item = respuesta.json()["results"][0]
        for campo in ("opinion", "score", "chair_comments"):
            self.assertNotIn(campo, item)