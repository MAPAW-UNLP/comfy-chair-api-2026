from reviewer.models import Review, ReviewVersion
from reviewer.serializers import (
    ReviewSerializer,
    ReviewUpdateSerializer,
    ReviewVersionSerializer,
    PublicReviewSerializer,
)
from .base import ReviewerTestCase


class ChairCommentsTestCase(ReviewerTestCase):
    def setUp(self):
        super().setUp()
        self.reviewer = self.crear_usuario()
        self.other_reviewer = self.crear_usuario()
        self.author = self.crear_usuario()
        self.conference = self.crear_conferencia()
        self.session = self.crear_sesion(conferencia=self.conference)
        self.article = self.crear_articulo(autor=self.author, sesion=self.session)
        self.assignment = self.crear_asignacion(self.reviewer, self.article)

    def auth(self, user):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token_para(user)}"}


class BaseReviewModelTests(ChairCommentsTestCase):
    def test_default_chair_comments_is_empty_string(self):
        """Issue 4.1-BE: Review y ReviewVersion almacenan '' por defecto en chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=2,
            opinion="Opinión de prueba",
        )
        self.assertEqual(review.chair_comments, "")

        version = ReviewVersion.objects.create(
            review=review,
            version_number=1,
            score=2,
            opinion="Opinión v1",
        )
        self.assertEqual(version.chair_comments, "")

    def test_explicit_chair_comments_persists(self):
        """Issue 4.1-BE: Comentarios explícitos se persisten correctamente"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=3,
            opinion="Excelente trabajo",
            chair_comments="Recomiendo premiar este trabajo",
        )
        self.assertEqual(review.chair_comments, "Recomiendo premiar este trabajo")


class ReviewSerializerTests(ChairCommentsTestCase):
    def test_serializers_include_chair_comments(self):
        """Issue 4.2-BE: Serializadores del dueño incluyen chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=2,
            opinion="Buena propuesta",
            chair_comments="Comentario confidencial",
        )
        data = ReviewSerializer(review).data
        self.assertIn("chair_comments", data)
        self.assertEqual(data["chair_comments"], "Comentario confidencial")

        update_serializer = ReviewUpdateSerializer(
            instance=review,
            data={"chair_comments": "Comentario confidencial editado"},
            partial=True,
        )
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)
        self.assertIn("chair_comments", update_serializer.fields)

        version = ReviewVersion.objects.create(
            review=review,
            version_number=1,
            score=1,
            opinion="Regular",
            chair_comments="Comentario v1",
        )
        v_data = ReviewVersionSerializer(version).data
        self.assertIn("chair_comments", v_data)
        self.assertEqual(v_data["chair_comments"], "Comentario v1")

    def test_public_serializer_excludes_chair_comments(self):
        """Issue 4.3-BE: PublicReviewSerializer no incluye chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=2,
            opinion="Opinión pública",
            chair_comments="SECRETO-CHAIR-CONFIDENCIAL",
            is_published=True,
        )
        data = PublicReviewSerializer(review).data
        self.assertNotIn("chair_comments", data)
        self.assertIn("score", data)
        self.assertIn("opinion", data)
        self.assertIn("reviewer", data)


class ReviewLifecycleAPITests(ChairCommentsTestCase):
    def test_create_draft_with_chair_comments(self):
        """Issue 4.2-BE: POST /api/reviews/ guarda borrador con chair_comments"""
        url = "/api/reviews/"
        payload = {
            "article": self.article.id,
            "score": 2,
            "opinion": "Buen borrador",
            "chair_comments": "Comentario preliminar para el chair",
        }
        res = self.client.post(url, payload, content_type="application/json", **self.auth(self.reviewer))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["chair_comments"], "Comentario preliminar para el chair")

    def test_update_draft_with_chair_comments(self):
        """Issue 4.2-BE: PUT /api/reviews/{id}/updateDraft/ actualiza chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=0,
            opinion="Dudoso",
            chair_comments="Comentario inicial",
            is_published=False,
        )
        url = f"/api/reviews/{review.id}/updateDraft/"
        payload = {
            "opinion": "Dudoso mejorado",
            "chair_comments": "Comentario actualizado",
        }
        res = self.client.put(url, payload, content_type="application/json", **self.auth(self.reviewer))
        self.assertEqual(res.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.chair_comments, "Comentario actualizado")

    def test_publish_creates_version_1_with_chair_comments(self):
        """Issue 4.2-BE: PUT /api/reviews/{id}/publish/ crea ReviewVersion v1 con chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=3,
            opinion="Excelente",
            chair_comments="Notas de aceptación para el chair",
            is_published=False,
        )
        url = f"/api/reviews/{review.id}/publish/"
        res = self.client.put(url, content_type="application/json", **self.auth(self.reviewer))
        self.assertEqual(res.status_code, 200)

        review.refresh_from_db()
        self.assertTrue(review.is_published)

        self.assignment.refresh_from_db()
        self.assertTrue(self.assignment.reviewed)

        versions = review.versions.all()
        self.assertEqual(versions.count(), 1)
        v1 = versions.first()
        self.assertEqual(v1.version_number, 1)
        self.assertEqual(v1.chair_comments, "Notas de aceptación para el chair")

    def test_update_published_creates_version_2_even_if_only_chair_comments_changed(self):
        """Issue 4.2-BE: PUT /api/reviews/{id}/updatePublished/ crea v n+1 aunque solo cambie chair_comments"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=3,
            opinion="Excelente",
            chair_comments="Comentario inicial",
            is_published=True,
        )
        ReviewVersion.objects.create(
            review=review,
            version_number=1,
            score=3,
            opinion="Excelente",
            chair_comments="Comentario inicial",
        )

        url = f"/api/reviews/{review.id}/updatePublished/"
        payload = {
            "chair_comments": "Comentario v2 actualizado",
        }
        res = self.client.put(url, payload, content_type="application/json", **self.auth(self.reviewer))
        self.assertEqual(res.status_code, 200)

        review.refresh_from_db()
        self.assertEqual(review.chair_comments, "Comentario v2 actualizado")

        versions = review.versions.order_by("version_number")
        self.assertEqual(versions.count(), 2)
        v2 = versions.last()
        self.assertEqual(v2.version_number, 2)
        self.assertEqual(v2.chair_comments, "Comentario v2 actualizado")

    def test_non_owner_forbidden_on_review_actions(self):
        """Issue 4.2-BE: Un revisor ajeno no puede modificar ni consultar versiones"""
        review = Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=2,
            opinion="Opinión",
            chair_comments="Secreto",
            is_published=False,
        )

        # Intento de editar borrador ajeno -> 403
        res = self.client.put(
            f"/api/reviews/{review.id}/updateDraft/",
            {"opinion": "Hack"},
            content_type="application/json",
            **self.auth(self.other_reviewer),
        )
        self.assertEqual(res.status_code, 403)

        # Intento de publicar review ajena -> 403
        res = self.client.put(
            f"/api/reviews/{review.id}/publish/",
            content_type="application/json",
            **self.auth(self.other_reviewer),
        )
        self.assertEqual(res.status_code, 403)

        # Publicar con el dueño
        self.client.put(
            f"/api/reviews/{review.id}/publish/",
            content_type="application/json",
            **self.auth(self.reviewer),
        )

        # Intento de editar review publicada ajena -> 403
        res = self.client.put(
            f"/api/reviews/{review.id}/updatePublished/",
            {"opinion": "Hack v2"},
            content_type="application/json",
            **self.auth(self.other_reviewer),
        )
        self.assertEqual(res.status_code, 403)

        # Intento de consultar versiones ajenas -> 403
        res = self.client.get(
            f"/api/reviews/{review.id}/versions/",
            **self.auth(self.other_reviewer),
        )
        self.assertEqual(res.status_code, 403)


class ConfidentialityLeakAuditTests(ChairCommentsTestCase):
    def test_public_articles_reviews_endpoint_does_not_leak_chair_comments(self):
        """Issue 4.3-BE: /api/article/<id>/reviews/ NUNCA expone chair_comments ni strings secretos"""
        secret_marker = "SECRETO-CHAIR-9a4f"
        Review.objects.create(
            reviewer=self.reviewer,
            article=self.article,
            score=1,
            opinion="Opinión pública para autores y revisores",
            chair_comments=secret_marker,
            is_published=True,
        )

        url = f"/api/article/{self.article.id}/reviews/"
        res = self.client.get(url, **self.auth(self.author))
        self.assertEqual(res.status_code, 200)

        # Validar que no aparezca el campo ni el contenido secreto
        self.assertNotIn(b"chair_comments", res.content)
        self.assertNotIn(secret_marker.encode("utf-8"), res.content)

        # Verificar estructura de respuesta
        self.assertEqual(res.data["article_id"], self.article.id)
        self.assertEqual(res.data["count"], 1)
        review_item = res.data["reviews"][0]
        self.assertNotIn("chair_comments", review_item)
        self.assertIn("score", review_item)
        self.assertIn("opinion", review_item)
