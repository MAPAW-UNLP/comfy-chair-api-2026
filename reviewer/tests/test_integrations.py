"""Ventanas de bidding y revisión por sesión (issue 5.1-BE)."""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from reviewer.integrations import Window, _aware, bidding_window, review_window

from .base import ReviewerTestCase

UTC = timezone.utc
INICIO = datetime(2026, 10, 1, tzinfo=UTC)
FIN = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)


class WindowTests(SimpleTestCase):
    def test_sin_limites_siempre_abierta(self):
        self.assertTrue(Window(None, None).is_open(INICIO))

    def test_antes_del_inicio_cerrada(self):
        self.assertFalse(Window(INICIO, FIN).is_open(INICIO - timedelta(microseconds=1)))

    def test_bordes_inclusivos(self):
        ventana = Window(INICIO, FIN)
        self.assertTrue(ventana.is_open(INICIO))
        self.assertTrue(ventana.is_open(FIN))

    def test_despues_del_fin_cerrada(self):
        self.assertFalse(Window(INICIO, FIN).is_open(FIN + timedelta(microseconds=1)))

    def test_solo_inicio_o_solo_fin(self):
        self.assertTrue(Window(INICIO, None).is_open(FIN + timedelta(days=365)))
        self.assertTrue(Window(None, FIN).is_open(INICIO - timedelta(days=365)))

    def test_sin_now_usa_el_reloj_del_servidor(self):
        with mock.patch("reviewer.integrations.timezone.now", return_value=FIN + timedelta(seconds=1)):
            self.assertFalse(Window(INICIO, FIN).is_open())
        with mock.patch("reviewer.integrations.timezone.now", return_value=INICIO):
            self.assertTrue(Window(INICIO, FIN).is_open())

    def test_as_dict_en_iso_utc(self):
        self.assertEqual(
            Window(INICIO, FIN).as_dict(INICIO),
            {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T23:59:59Z", "is_open": True},
        )
        self.assertEqual(Window(None, None).as_dict(), {"start": None, "end": None, "is_open": True})


class AwareTests(SimpleTestCase):
    def test_none(self):
        self.assertIsNone(_aware(None))

    def test_date_de_inicio_arranca_a_medianoche_utc(self):
        self.assertEqual(_aware(date(2026, 10, 1)), INICIO)

    def test_date_de_fin_cuenta_el_dia_completo(self):
        self.assertEqual(
            _aware(date(2026, 10, 31), end_of_day=True),
            datetime.combine(date(2026, 10, 31), time.max, tzinfo=UTC),
        )

    def test_datetime_naive_se_toma_como_utc(self):
        self.assertEqual(_aware(datetime(2026, 10, 1, 12, 0)), datetime(2026, 10, 1, 12, 0, tzinfo=UTC))

    def test_datetime_aware_se_convierte_a_utc(self):
        local = datetime(2026, 10, 1, 21, 0, tzinfo=timezone(timedelta(hours=-3)))
        convertido = _aware(local)
        self.assertEqual(convertido, datetime(2026, 10, 2, 0, 0, tzinfo=UTC))
        self.assertEqual(convertido.utcoffset(), timedelta(0))

    def test_datetime_con_end_of_day_no_se_modifica(self):
        self.assertEqual(_aware(FIN, end_of_day=True), FIN)


class VentanasPorSesionTests(SimpleTestCase):
    def test_session_none_ventana_abierta(self):
        self.assertEqual(review_window(None), Window(None, None))
        self.assertEqual(bidding_window(None), Window(None, None))

    def test_sesion_sin_campos_ni_conferencia_ventana_abierta(self):
        self.assertEqual(review_window(SimpleNamespace()), Window(None, None))

    def test_lee_los_campos_de_la_sesion(self):
        sesion = SimpleNamespace(
            review_start=date(2026, 10, 1), review_end=date(2026, 10, 31),
            bidding_start=date(2026, 9, 1), bidding_end=date(2026, 9, 15),
        )
        self.assertEqual(review_window(sesion).start, INICIO)
        self.assertEqual(review_window(sesion).end.date(), date(2026, 10, 31))
        self.assertEqual(bidding_window(sesion).start, datetime(2026, 9, 1, tzinfo=UTC))
        self.assertEqual(bidding_window(sesion).end.date(), date(2026, 9, 15))

    def test_sin_campos_en_la_sesion_usa_los_de_la_conferencia(self):
        conferencia = SimpleNamespace(review_start=date(2026, 10, 1), review_end=date(2026, 10, 31))
        ventana = review_window(SimpleNamespace(conference=conferencia))
        self.assertEqual(ventana.start, INICIO)
        self.assertEqual(ventana.end, datetime.combine(date(2026, 10, 31), time.max, tzinfo=UTC))

    def test_campo_de_la_sesion_tiene_prioridad_sobre_la_conferencia(self):
        conferencia = SimpleNamespace(review_start=date(2026, 9, 1), review_end=date(2026, 9, 30))
        sesion = SimpleNamespace(review_start=None, review_end=date(2026, 10, 31), conference=conferencia)
        ventana = review_window(sesion)
        self.assertEqual(ventana.start, datetime(2026, 9, 1, tzinfo=UTC))
        self.assertEqual(ventana.end.date(), date(2026, 10, 31))

    def test_bidding_sin_campos_en_ningun_lado_queda_abierto(self):
        conferencia = SimpleNamespace(review_start=date(2026, 10, 1), review_end=date(2026, 10, 31))
        self.assertEqual(bidding_window(SimpleNamespace(conference=conferencia)), Window(None, None))


class VentanaConModelosRealesTests(ReviewerTestCase):
    def test_session_real_toma_el_periodo_de_su_conferencia(self):
        hoy = date.today()
        conferencia = self.crear_conferencia(review_start=hoy - timedelta(days=1), review_end=hoy - timedelta(days=1))
        ventana = review_window(self.crear_sesion(conferencia))
        self.assertEqual(ventana.start.date(), hoy - timedelta(days=1))
        self.assertFalse(ventana.is_open(datetime.combine(hoy, time.min, tzinfo=UTC)))
        self.assertTrue(ventana.is_open(datetime.combine(hoy, time.min, tzinfo=UTC) - timedelta(microseconds=1)))

    def test_conferencia_sin_periodo_ventana_abierta(self):
        self.assertEqual(review_window(self.crear_sesion()), Window(None, None))
