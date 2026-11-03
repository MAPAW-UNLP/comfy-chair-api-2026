"""GET /api/reviewer/sessions/<session_id>/periods/ (issue 5.2-BE)."""
from datetime import date, datetime, timezone
from unittest import mock

from django.urls import reverse

from .base import ReviewerTestCase

AHORA = datetime(2026, 10, 19, 15, 30, tzinfo=timezone.utc)


class PeriodosDeSesionTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.usuario = self.crear_usuario()
        self.cliente = self.cliente_de(self.usuario)

    def url(self, session_id):
        return reverse("reviewer-session-periods", args=[session_id])

    def consultar(self, sesion):
        with mock.patch("reviewer.api.timezone.now", return_value=AHORA):
            respuesta = self.cliente.get(self.url(sesion.id))
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.json()

    def test_sin_token_401(self):
        respuesta = self.client.get(self.url(self.crear_sesion().id))
        self.assertEqual(respuesta.status_code, 401)

    def test_sesion_inexistente_404(self):
        respuesta = self.cliente.get(self.url(999999))
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.json(), {"error": "Sesión no encontrada"})

    def test_sin_fechas_configuradas_ventanas_abiertas(self):
        sesion = self.crear_sesion()
        self.assertEqual(
            self.consultar(sesion),
            {
                "session_id": sesion.id,
                "server_now": "2026-10-19T15:30:00Z",
                "bidding": {"start": None, "end": None, "is_open": True},
                "review": {"start": None, "end": None, "is_open": True},
            },
        )

    def test_periodo_de_revision_vigente(self):
        conferencia = self.crear_conferencia(
            start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
            review_start=date(2026, 10, 1), review_end=date(2026, 10, 19),
        )
        datos = self.consultar(self.crear_sesion(conferencia))
        self.assertEqual(
            datos["review"],
            {"start": "2026-10-01T00:00:00Z", "end": "2026-10-19T23:59:59.999999Z", "is_open": True},
        )
        self.assertEqual(datos["bidding"], {"start": None, "end": None, "is_open": True})

    def test_periodo_de_revision_vencido(self):
        conferencia = self.crear_conferencia(
            start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
            review_start=date(2026, 9, 1), review_end=date(2026, 10, 18),
        )
        self.assertFalse(self.consultar(self.crear_sesion(conferencia))["review"]["is_open"])

    def test_periodo_de_revision_no_iniciado(self):
        conferencia = self.crear_conferencia(
            start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
            review_start=date(2026, 10, 20), review_end=date(2026, 11, 30),
        )
        self.assertFalse(self.consultar(self.crear_sesion(conferencia))["review"]["is_open"])
