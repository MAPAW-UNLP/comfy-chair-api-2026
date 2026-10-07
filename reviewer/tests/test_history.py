"""Tests de GET /api/reviews/reviewer/<id>/?conference_id= (issue 3.2-BE)."""
from django.urls import reverse

from .base import ReviewerTestCase


class ReviewerHistoryFilterTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.revisor = self.crear_usuario()
        self.cliente = self.cliente_de(self.revisor)

    # ---------- utilidades ----------

    def url_de(self, revisor=None):
        revisor = revisor or self.revisor
        return reverse("review-by-reviewer-id", kwargs={"reviewerId": revisor.id})

    def pedir(self, revisor=None, **params):
        return self.cliente.get(self.url_de(revisor), params)

    def revisar(self, sesion, publicada=True, revisor=None):
        """Crea un artículo en la sesión (o sin sesión) y una review del revisor."""
        articulo = self.crear_articulo(sesion=sesion, con_sesion=False)
        return self.crear_review(revisor or self.revisor, articulo, publicada=publicada)

    def ids(self, respuesta):
        return [r["id"] for r in respuesta.json()]

    # ---------- acceso ----------

    def test_sin_token_responde_401(self):
        self.assertEqual(self.client.get(self.url_de()).status_code, 401)

    # ---------- comportamiento sin filtro (no debe cambiar) ----------

    def test_sin_parametro_devuelve_las_publicadas_de_todas_las_conferencias(self):
        en_a = self.revisar(self.crear_sesion())
        en_b = self.revisar(self.crear_sesion())
        respuesta = self.pedir()
        self.assertEqual(respuesta.status_code, 200)
        self.assertCountEqual(self.ids(respuesta), [en_a.id, en_b.id])

    def test_parametro_vacio_se_trata_como_sin_filtro(self):
        en_a = self.revisar(self.crear_sesion())
        en_b = self.revisar(self.crear_sesion())
        respuesta = self.pedir(conference_id="")
        self.assertEqual(respuesta.status_code, 200)
        self.assertCountEqual(self.ids(respuesta), [en_a.id, en_b.id])

    # ---------- filtro por conferencia ----------

    def test_con_conference_id_devuelve_solo_las_de_esa_conferencia(self):
        sesion_a = self.crear_sesion()
        sesion_b = self.crear_sesion()
        en_a = self.revisar(sesion_a)
        self.revisar(sesion_b)
        respuesta = self.pedir(conference_id=sesion_a.conference_id)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.ids(respuesta), [en_a.id])

    def test_los_borradores_no_aparecen_aunque_sean_de_la_conferencia(self):
        sesion = self.crear_sesion()
        publicada = self.revisar(sesion)
        self.revisar(sesion, publicada=False)
        respuesta = self.pedir(conference_id=sesion.conference_id)
        self.assertEqual(self.ids(respuesta), [publicada.id])

    def test_no_aparecen_reviews_de_otro_revisor(self):
        sesion = self.crear_sesion()
        propia = self.revisar(sesion)
        self.revisar(sesion, revisor=self.crear_usuario())
        respuesta = self.pedir(conference_id=sesion.conference_id)
        self.assertEqual(self.ids(respuesta), [propia.id])

    def test_articulo_sin_sesion_no_aparece_con_filtro_pero_si_sin_filtro(self):
        sin_sesion = self.revisar(None)
        sesion = self.crear_sesion()
        en_sesion = self.revisar(sesion)
        self.assertCountEqual(self.ids(self.pedir()), [sin_sesion.id, en_sesion.id])
        con_filtro = self.pedir(conference_id=sesion.conference_id)
        self.assertEqual(self.ids(con_filtro), [en_sesion.id])

    # ---------- sin resultados y errores ----------

    def test_conferencia_sin_reviews_del_revisor_responde_404(self):
        self.revisar(self.crear_sesion())
        otra_conferencia = self.crear_conferencia()
        respuesta = self.pedir(conference_id=otra_conferencia.id)
        self.assertEqual(respuesta.status_code, 404)
        self.assertIn("message", respuesta.json())

    def test_conference_id_no_numerico_responde_400(self):
        respuesta = self.pedir(conference_id="abc")
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("error", respuesta.json())