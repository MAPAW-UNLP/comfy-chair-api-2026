"""GET /api/article/<id>/reviews/confidential/ para Chairs (issue 4.4-BE)."""
from django.urls import reverse

from .base import ReviewerTestCase

SECRETO = "COMENTARIO-SOLO-PARA-CHAIRS-4.4"
SECRETO_BORRADOR = "BORRADOR-SECRETO-4.4"
SIN_PERMISO = "No tenés permiso de Chair para ver los comentarios confidenciales de este artículo"


class ReviewsConfidencialesTests(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.autor = self.crear_usuario()
        self.revisor = self.crear_usuario()
        self.otro_revisor = self.crear_usuario()
        self.chair_sesion = self.crear_usuario()
        self.chair_conferencia = self.crear_usuario()
        self.conferencia = self.crear_conferencia()
        self.conferencia.chairs.add(self.chair_conferencia)
        self.sesion = self.crear_sesion(self.conferencia)
        self.sesion.chairs.add(self.chair_sesion)
        self.articulo = self.crear_articulo(autor=self.autor, sesion=self.sesion)
        self.crear_asignacion(self.revisor, self.articulo)
        self.crear_asignacion(self.otro_revisor, self.articulo)
        self.publicada = self.crear_review(self.revisor, self.articulo, publicada=True, chair_comments=SECRETO)
        self.crear_review(self.otro_revisor, self.articulo, chair_comments=SECRETO_BORRADOR)

    def consultar(self, usuario, article_id=None):
        url = reverse("reviews-article-confidential", args=[article_id or self.articulo.id])
        return self.cliente_de(usuario).get(url)

    def test_chair_de_la_sesion_ve_chair_comments(self):
        respuesta = self.consultar(self.chair_sesion)
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual(datos["article_id"], self.articulo.id)
        review = datos["reviews"][0]
        self.assertEqual(
            set(review),
            {"id", "article", "reviewer", "reviewer_name", "score", "opinion", "chair_comments", "created_at", "updated_at"},
        )
        self.assertEqual(review["id"], self.publicada.id)
        self.assertEqual(review["reviewer_name"], self.revisor.full_name)
        self.assertEqual(review["chair_comments"], SECRETO)

    def test_chair_de_la_conferencia_ve_chair_comments(self):
        respuesta = self.consultar(self.chair_conferencia)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(SECRETO, respuesta.content.decode())

    def test_solo_devuelve_reviews_publicadas(self):
        respuesta = self.consultar(self.chair_sesion)
        self.assertEqual(len(respuesta.json()["reviews"]), 1)
        self.assertNotIn(SECRETO_BORRADOR, respuesta.content.decode())

    def test_autor_y_revisores_reciben_403_sin_fuga(self):
        for usuario in (self.autor, self.revisor, self.otro_revisor, self.crear_usuario()):
            respuesta = self.consultar(usuario)
            self.assertEqual(respuesta.status_code, 403)
            self.assertEqual(respuesta.json(), {"error": SIN_PERMISO})
            self.assertNotIn(SECRETO, respuesta.content.decode())

    def test_chair_de_otra_sesion_recibe_403(self):
        otra_sesion = self.crear_sesion(self.conferencia)
        chair_ajeno = self.crear_usuario()
        otra_sesion.chairs.add(chair_ajeno)
        self.assertEqual(self.consultar(chair_ajeno).status_code, 403)

    def test_articulo_sin_sesion_403(self):
        articulo = self.crear_articulo(con_sesion=False)
        self.assertEqual(self.consultar(self.chair_sesion, articulo.id).status_code, 403)

    def test_articulo_inexistente_404(self):
        respuesta = self.consultar(self.chair_sesion, 999999)
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.json(), {"error": "Artículo no encontrado"})

    def test_sin_token_401(self):
        url = reverse("reviews-article-confidential", args=[self.articulo.id])
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 401)
        self.assertNotIn(SECRETO, respuesta.content.decode())

    def test_endpoint_publico_sigue_sin_exponer_chair_comments(self):
        url = reverse("reviews-article", args=[self.articulo.id])
        respuesta = self.cliente_de(self.chair_sesion).get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn(SECRETO, respuesta.content.decode())
