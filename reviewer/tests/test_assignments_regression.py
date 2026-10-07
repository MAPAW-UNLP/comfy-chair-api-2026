"""Tests de regresión de asignaciones por conferencia (issue 3.3-BE).

A diferencia de test_assignments.py (que prueba el endpoint por partes), acá se
recorre el camino completo: el revisor avanza (borrador -> publicada) usando los
endpoints reales y se verifica que GET /api/reviewer/assignments/ refleje cada paso.
"""
from unittest import mock

from django.db import IntegrityError, transaction
from django.urls import reverse

from chair.models import ReviewAssignment

from .base import ReviewerTestCase


class AsignacionesRegresionTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.revisor = self.crear_usuario()
        self.conferencia = self.crear_conferencia()
        self.sesion_1 = self.crear_sesion(self.conferencia)
        self.sesion_2 = self.crear_sesion(self.conferencia)
        self.url = reverse("reviewer-assignments")

    # ---------- utilidades ----------

    def asignar(self, sesion=None, revisor=None):
        """Crea un artículo (en la sesión dada, o sin sesión) y se lo asigna."""
        articulo = self.crear_articulo(sesion=sesion, con_sesion=False)
        self.crear_asignacion(revisor or self.revisor, articulo)
        return articulo

    def consultar(self, revisor=None, **params):
        cliente = self.cliente_de(revisor or self.revisor)
        respuesta = cliente.get(self.url, params)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    # Los dos únicos puntos que usan los endpoints de reviews actuales. Hoy el
    # POST recibe `reviewer` en el body, y el PUT recibe `id` en la URL. En el futuro se puede cambiar a
    # /api/reviewer/reviews/<review_id>/publish/ y no pasar `review
    def crear_borrador(self, revisor, articulo):
        respuesta = self.cliente_de(revisor).post(
            reverse("create-review"),
            {
                "reviewer": revisor.id,
                "article": articulo.id,
                "score": 1,
                "opinion": "Opinión en borrador",
            },
            format="json",
        )
        self.assertEqual(respuesta.status_code, 201)
        return respuesta.json()["id"]

    def publicar(self, revisor, review_id):
        respuesta = self.cliente_de(revisor).put(
            reverse("review-publish", kwargs={"id": review_id})
        )
        self.assertEqual(respuesta.status_code, 200)

    @staticmethod
    def resumen(stats):
        return {
            "pending": stats["pending"],
            "draft": stats["draft"],
            "published": stats["published"],
        }

    @staticmethod
    def progreso(stats):
        """{session_id: (total, publicadas)} a partir de by_session."""
        return {f["session_id"]: (f["total"], f["published"]) for f in stats["by_session"]}

    # ---------- 1. flujo completo ----------

    def test_flujo_completo_pendiente_borrador_publicada(self):
        a1 = self.asignar(self.sesion_1)
        self.asignar(self.sesion_1)
        a3 = self.asignar(self.sesion_2)

        # Al inicio, todo pendiente.
        stats = self.consultar()["stats"]
        self.assertEqual(self.resumen(stats), {"pending": 3, "draft": 0, "published": 0})
        self.assertEqual(
            self.progreso(stats), {self.sesion_1.id: (2, 0), self.sesion_2.id: (1, 0)}
        )

        # Guarda un borrador de a1.
        review_id = self.crear_borrador(self.revisor, a1)
        stats = self.consultar()["stats"]
        self.assertEqual(self.resumen(stats), {"pending": 2, "draft": 1, "published": 0})
        self.assertEqual(
            self.progreso(stats), {self.sesion_1.id: (2, 0), self.sesion_2.id: (1, 0)}
        )

        # Publica a1.
        self.publicar(self.revisor, review_id)
        respuesta = self.consultar()
        self.assertEqual(
            self.resumen(respuesta["stats"]), {"pending": 2, "draft": 0, "published": 1}
        )
        self.assertEqual(
            self.progreso(respuesta["stats"]),
            {self.sesion_1.id: (2, 1), self.sesion_2.id: (1, 0)},
        )
        item = next(r for r in respuesta["results"] if r["article"]["id"] == a1.id)
        self.assertEqual(item["review_status"], "published")
        self.assertEqual(item["review_id"], review_id)

        # Publica también a3 (la otra sesión).
        self.publicar(self.revisor, self.crear_borrador(self.revisor, a3))
        stats = self.consultar()["stats"]
        self.assertEqual(self.resumen(stats), {"pending": 1, "draft": 0, "published": 2})
        self.assertEqual(
            self.progreso(stats), {self.sesion_1.id: (2, 1), self.sesion_2.id: (1, 1)}
        )

    # ---------- 2. varias conferencias ----------

    def test_varias_conferencias_cada_filtro_devuelve_lo_suyo(self):
        otra_conferencia = self.crear_conferencia()
        sesion_otra = self.crear_sesion(otra_conferencia)
        en_primera = self.asignar(self.sesion_1)
        en_otra = self.asignar(sesion_otra)
        self.publicar(self.revisor, self.crear_borrador(self.revisor, en_otra))

        de_primera = self.consultar(conference_id=self.conferencia.id)
        de_otra = self.consultar(conference_id=otra_conferencia.id)
        todas = self.consultar()

        self.assertEqual([r["article"]["id"] for r in de_primera["results"]], [en_primera.id])
        self.assertEqual(de_primera["stats"]["total"], 1)
        self.assertEqual([r["article"]["id"] for r in de_otra["results"]], [en_otra.id])
        self.assertEqual(de_otra["stats"]["published"], 1)
        # Sin filtro, el total es la suma de las dos.
        self.assertEqual(
            todas["stats"]["total"],
            de_primera["stats"]["total"] + de_otra["stats"]["total"],
        )

    # ---------- 3. reasignación ----------

    def test_asignacion_borrada_y_recreada_aparece_una_sola_vez(self):
        articulo = self.asignar(self.sesion_1)
        asignacion = ReviewAssignment.objects.get(reviewer=self.revisor, article=articulo)

        # El chair la da de baja: desaparece de la lista.
        asignacion.deleted = True
        asignacion.save()
        self.assertEqual(self.consultar()["results"], [])

        # El chair la vuelve a asignar (simulado con update_or_create): vuelve a
        # aparecer, una sola vez.
        ReviewAssignment.objects.update_or_create(
            reviewer=self.revisor, article=articulo, defaults={"deleted": False}
        )
        respuesta = self.consultar()
        self.assertEqual([r["article"]["id"] for r in respuesta["results"]], [articulo.id])
        self.assertEqual(respuesta["stats"]["total"], 1)
        self.assertEqual(
            ReviewAssignment.objects.filter(reviewer=self.revisor, article=articulo).count(), 1
        )

    def test_la_base_impide_asignaciones_duplicadas(self):
        articulo = self.asignar(self.sesion_1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.crear_asignacion(self.revisor, articulo)

    # ---------- 4. artículo sin sesión ----------

    def test_articulo_sin_sesion_recorre_los_estados_sin_romper(self):
        articulo = self.asignar(sesion=None)

        item = self.consultar()["results"][0]
        self.assertEqual(item["review_status"], "pending")
        self.assertIsNone(item["session"])
        self.assertIsNone(item["conference"])
        self.assertTrue(item["review_period"]["is_open"])

        self.publicar(self.revisor, self.crear_borrador(self.revisor, articulo))
        respuesta = self.consultar()
        self.assertEqual(respuesta["results"][0]["review_status"], "published")
        self.assertEqual(respuesta["stats"]["total"], 1)
        self.assertEqual(respuesta["stats"]["published"], 1)
        self.assertEqual(respuesta["stats"]["by_session"], [])

        # Al filtrar por conferencia, un artículo sin sesión no aparece.
        self.assertEqual(self.consultar(conference_id=self.conferencia.id)["results"], [])

    # ---------- 5. período ----------

    def test_sin_periodo_configurado_la_ventana_esta_abierta(self):
        self.asignar(self.sesion_1)
        periodo = self.consultar()["results"][0]["review_period"]
        self.assertEqual(periodo, {"start": None, "end": None, "is_open": True})

    def test_review_period_refleja_la_ventana_de_cada_sesion(self):
        self.asignar(self.sesion_1)
        self.asignar(self.sesion_2)
        cerrada = {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z", "is_open": False}
        abierta = {"start": None, "end": "2099-01-01T00:00:00Z", "is_open": True}

        def ventana(sesion):
            return cerrada if sesion.id == self.sesion_1.id else abierta

        with mock.patch("reviewer.serializers.review_window", side_effect=ventana):
            resultados = self.consultar()["results"]

        por_sesion = {r["session"]["id"]: r["review_period"] for r in resultados}
        self.assertEqual(por_sesion[self.sesion_1.id], cerrada)
        self.assertEqual(por_sesion[self.sesion_2.id], abierta)

    # ---------- 6. aislamiento entre revisores ----------

    def test_dos_revisores_del_mismo_articulo_ven_cada_uno_su_estado(self):
        otro = self.crear_usuario()
        articulo = self.crear_articulo(sesion=self.sesion_1)
        self.crear_asignacion(self.revisor, articulo)
        self.crear_asignacion(otro, articulo)

        self.publicar(self.revisor, self.crear_borrador(self.revisor, articulo))

        mio = self.consultar(self.revisor)
        suyo = self.consultar(otro)
        self.assertEqual(mio["results"][0]["review_status"], "published")
        self.assertEqual(suyo["results"][0]["review_status"], "pending")
        self.assertIsNone(suyo["results"][0]["review_id"])
        self.assertEqual(self.resumen(suyo["stats"]), {"pending": 1, "draft": 0, "published": 0})