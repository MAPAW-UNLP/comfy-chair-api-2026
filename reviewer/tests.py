from datetime import date, datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from article.models import Article
from conference.models import Conference
from conference_session.models import Session
from notification.models import Notification
from reviewer.models import Bid
from user.models import User


class BidTestCase(TestCase):
    def setUp(self):
        self.revisor = self.crear_usuario("revisor@test.com")
        self.autor = self.crear_usuario("autor@test.com")
        self.conferencia = self.crear_conferencia("Conferencia A")
        self.sesion = self.crear_sesion(self.conferencia, "Sesión A1")
        self.articulo = self.crear_articulo(self.sesion)

    def crear_usuario(self, email):
        return User.objects.create(email=email, full_name=email, affiliation="UNLP", role="user")

    def crear_conferencia(self, titulo):
        hoy = date.today()
        return Conference.objects.create(
            title=titulo, description="-", start_date=hoy, end_date=hoy + timedelta(days=30)
        )

    def crear_sesion(self, conferencia, titulo):
        return Session.objects.create(
            title=titulo, deadline=conferencia.end_date, capacity=10, conference=conferencia
        )

    def crear_articulo(self, sesion):
        return Article.objects.create(
            title="Artículo", type="regular", abstract="-", session=sesion, corresponding_author=self.autor
        )

    def cliente(self, usuario):
        exp = datetime.now(timezone.utc) + timedelta(hours=1)
        token = jwt.encode(
            {"user_id": usuario.id, "exp": exp}, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )
        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return cliente

    def postear(self, choice="Interesado", **extra):
        datos = {"reviewer": self.revisor.id, "article": self.articulo.id, "choice": choice, **extra}
        return self.cliente(self.revisor).post("/api/bidding/", datos, format="json")


class BiddingUpsertTests(BidTestCase):
    def test_primer_post_crea_el_bid_y_responde_201(self):
        respuesta = self.postear()
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["choice"], "Interesado")
        self.assertEqual(Bid.objects.count(), 1)

    def test_repetir_post_actualiza_y_responde_200(self):
        self.postear()
        respuesta = self.postear("Quizás")
        self.assertEqual(respuesta.status_code, 200)
        bids = Bid.objects.filter(reviewer=self.revisor, article=self.articulo)
        self.assertEqual(bids.count(), 1)
        self.assertEqual(bids.get().choice, "Quizás")

    def test_acepta_todas_las_opciones_validas(self):
        for choice, _ in Bid.STATE_CHOICES:
            with self.subTest(choice=choice):
                self.assertIn(self.postear(choice).status_code, (200, 201))
                self.assertEqual(Bid.objects.get().choice, choice)

    def test_choice_invalido_responde_400(self):
        for choice in ("interested", "", None):
            with self.subTest(choice=choice):
                respuesta = self.postear(choice)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json(), {"error": "Opción de interés inválida"})
        self.assertFalse(Bid.objects.exists())

    def test_articulo_inexistente_responde_400(self):
        respuesta = self.postear(article=999999)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {"error": "Opción de interés inválida"})

    def test_la_base_rechaza_bids_duplicados(self):
        Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Interesado")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Quizás")


class BiddingNotificacionTests(BidTestCase):
    def notificaciones(self):
        return list(
            Notification.objects.filter(user=self.revisor).order_by("id").values_list("title", flat=True)
        )

    def test_crear_y_actualizar_notifican_una_vez_cada_uno(self):
        self.postear()
        self.assertEqual(self.notificaciones(), ["Bidding realizado"])
        self.postear("Quizás")
        self.assertEqual(self.notificaciones(), ["Bidding realizado", "Bidding actualizado"])

    def test_un_400_no_notifica(self):
        self.postear("interested")
        self.assertEqual(self.notificaciones(), [])


class BidPropiedadTests(BidTestCase):
    def setUp(self):
        super().setUp()
        self.otro = self.crear_usuario("otro@test.com")

    def test_post_a_nombre_de_otro_queda_a_nombre_propio(self):
        respuesta = self.cliente(self.otro).post(
            "/api/bidding/",
            {"reviewer": self.revisor.id, "article": self.articulo.id, "choice": "Interesado"},
            format="json",
        )
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.data["reviewer"], self.otro.id)
        self.assertEqual(Bid.objects.get().reviewer, self.otro)

    def test_post_sin_reviewer_usa_el_del_token(self):
        respuesta = self.cliente(self.revisor).post(
            "/api/bidding/", {"article": self.articulo.id, "choice": "Quizás"}, format="json"
        )
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(Bid.objects.get().reviewer, self.revisor)

    def test_put_sobre_bid_propio_responde_200(self):
        bid = Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Interesado")
        respuesta = self.cliente(self.revisor).put(f"/api/bidding/{bid.id}/", {"choice": "Quizás"}, format="json")
        self.assertEqual(respuesta.status_code, 200)
        bid.refresh_from_db()
        self.assertEqual(bid.choice, "Quizás")

    def test_put_sobre_bid_ajeno_responde_403_y_no_lo_modifica(self):
        bid = Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Interesado")
        respuesta = self.cliente(self.otro).put(f"/api/bidding/{bid.id}/", {"choice": "Quizás"}, format="json")
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json(), {"error": "No podés modificar un bid ajeno"})
        bid.refresh_from_db()
        self.assertEqual(bid.choice, "Interesado")

    def test_put_sobre_bid_inexistente_responde_404(self):
        respuesta = self.cliente(self.revisor).put("/api/bidding/999999/", {"choice": "Quizás"}, format="json")
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.json(), {"error": "Bid no encontrado"})

    def test_get_lista_solo_los_bids_propios(self):
        propio = Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Interesado")
        Bid.objects.create(reviewer=self.otro, article=self.articulo, choice="Quizás")
        respuesta = self.cliente(self.revisor).get("/api/bids/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([b["id"] for b in respuesta.data], [propio.id])

    def test_get_con_reviewer_id_propio_responde_200(self):
        Bid.objects.create(reviewer=self.revisor, article=self.articulo, choice="Interesado")
        respuesta = self.cliente(self.revisor).get("/api/bids/", {"reviewerId": self.revisor.id})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.data), 1)

    def test_get_con_reviewer_id_ajeno_responde_403(self):
        respuesta = self.cliente(self.otro).get("/api/bids/", {"reviewerId": self.revisor.id})
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json(), {"error": "No podés ver los bids de otro revisor"})

    def test_sin_token_responde_401(self):
        anonimo = APIClient()
        self.assertEqual(anonimo.get("/api/bids/").status_code, 401)
        self.assertEqual(anonimo.post("/api/bidding/", {}, format="json").status_code, 401)
        self.assertEqual(anonimo.put("/api/bidding/1/", {}, format="json").status_code, 401)
