#from datetime import timezone
from django.utils import timezone
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from reviewer.models import Review, Article, Bid, ReviewVersion, User
from chair.models import ReviewAssignment
from reviewer.serializers import (
    ReviewUpdateSerializer,
    ReviewerDetailSerializer,
    BidSerializer,
    BidUpdateSerializer,
    ReviewSerializer,
    ReviewVersionSerializer,
    PublicReviewSerializer,
)

# # GET /api/articles
# class ArticleListView(APIView):
#     def get(self, request):
#         articles = Article.objects.all()
#         serializer = ArticleSerializer(articles, many=True)
#         return Response(serializer.data)

# # GET /api/articles/{id}
# class ArticleDetailView(APIView):
#     def get_object(self, pk):
#         try:
#             return Article.objects.get(pk=pk)
#         except Article.DoesNotExist:
#             raise Http404

#     def get(self, request, pk):
#         article = self.get_object(pk)
#         serializer = ArticleSerializer(article)
#         return Response(serializer.data)

# POST /api/bidding
class BiddingView(APIView):
    def post(self, request):
        serializer = BidSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

# PUT /api/bidding/{id}
class BiddingUpdateView(APIView):
    def put(self, request, id):
        #Busca el bid con el id, si no lo encuentra retorna 404
        bid = get_object_or_404(Bid, id=id)
        serializer = BidUpdateSerializer(bid, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# GET /api/bids?reviewerId=123
class ReviewerBidsView(APIView):
    def get(self, request):
        reviewer_id = request.GET.get('reviewerId')
        if not reviewer_id:
            return Response({"error": "reviewerId parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        #Se omite la verificacion del id, al no tener el modelo de User
        bids = Bid.objects.filter(reviewer_id=reviewer_id)
        serializer = BidSerializer(bids, many=True)
        return Response(serializer.data)

       
# GET /api/reviewers/{id}
class ReviewerDetailView(APIView):
    def get(self,request,id):
        try:
            # Buscar cualquier usuario por ID, ver si agregar verificacion de roles mas adelante..
            reviewer = User.objects.get(id=id)
            serializer = ReviewerDetailSerializer(reviewer)
            return Response(serializer.data)
        except User.DoesNotExist:
            return Response(
                {"error": f"Usuario con ID {id} no encontrado"}, 
                status=status.HTTP_404_NOT_FOUND
            )

#POST /api/reviews/
#Guarda una nueva revisión en borrador
class ReviewView(APIView):
    def post(self, request):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        user_id = getattr(request, "user_id", None)
        if user_id is not None:
            if "reviewer" not in data:
                data["reviewer"] = user_id
            elif int(data["reviewer"]) != user_id:
                return Response(
                    {"error": "No tenés permiso para crear una revisión para otro revisor"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        serializer = ReviewSerializer(data=data)
        if serializer.is_valid():   
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


#GET /api/review/{articleId}/
#Devuelve la revisión publicado o en borrador de un artículo
class ReviewDetailView(APIView):
    def get(self, request, articleId):
        review = Review.objects.filter(article=articleId).first()
        if not review:
            return Response(
               {"message":"No existe una revision de ese articulo"},
               status = status.HTTP_404_NOT_FOUND
           )
        serializer = ReviewSerializer(review)
        return Response(serializer.data,status=status.HTTP_200_OK)


# GET /api/reviews/reviewer/{reviewerId}/
#Devuelve todas las revisiones publicadas de un revisor
class ReviewsByReviewerIdView(APIView):
    def get(self, request, reviewerId):
        reviews = Review.objects.filter(reviewer_id=reviewerId, is_published=True)
        if not reviews.exists():
            return Response(
                {"message": "No se encontraron revisiones para este revisor"},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = ReviewSerializer(reviews, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
   
# GET /api/reviews/{idReview}/versions/
#Devuelve todas las versiones de una revisión
class ReviewVersionsView(APIView):
    def get(self, request, idReview):
        review = get_object_or_404(Review, id=idReview)
        user_id = getattr(request, "user_id", None)
        if user_id is not None and review.reviewer_id != user_id:
            return Response(
                {"error": "No tenés permiso sobre esta revisión"},
                status=status.HTTP_403_FORBIDDEN,
            )
        versions = ReviewVersion.objects.filter(review=review).order_by('version_number')
        if not versions.exists():
            return Response(
                {"message": "No hay versiones disponibles para esta revisión"},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = ReviewVersionSerializer(versions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
#PUT /api/reviews/{idReview}/publish/
class ReviewPublishView(APIView):
    def put(self, request, id):
        review = get_object_or_404(Review, id=id)
        user_id = getattr(request, "user_id", None)
        if user_id is not None and review.reviewer_id != user_id:
            return Response(
                {"error": "No tenés permiso sobre esta revisión"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if review.is_published:
            return Response(
                {"error": "La revisión ya está publicada"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if review.score is None:
            return Response(
                {"error": "La revisión debe tener una puntuación"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not review.opinion:
            return Response(
                {"error": "La revisión debe tener una opinión"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            try:
                assignment = ReviewAssignment.objects.get(
                    reviewer=review.reviewer,
                    article=review.article,
                )
                assignment.reviewed = True
                assignment.save()
            except ReviewAssignment.DoesNotExist:
                return Response(
                    {"error": "No se encontró la asignación de revisión"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            review.is_published = True
            review.created_at = timezone.now()
            review.save()
            ReviewVersion.objects.create(
                review=review,
                version_number=1,
                score=review.score,
                opinion=review.opinion,
                chair_comments=review.chair_comments,
            )

        serializer = ReviewSerializer(review)
        return Response(serializer.data, status=status.HTTP_200_OK)


# PUT api/reviews/{idReview}/updateDraft/
class ReviewUpdateDraftView(APIView):
    def put(self, request, id):
        review = get_object_or_404(Review, id=id)
        user_id = getattr(request, "user_id", None)
        if user_id is not None and review.reviewer_id != user_id:
            return Response(
                {"error": "No tenés permiso sobre esta revisión"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if review.is_published:
            return Response(
                {"error": "Usa el endpoint para revisiones publicadas"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewUpdateSerializer(review, data=request.data, partial=True)
        if serializer.is_valid():
            updated_review = serializer.save()
            return Response(ReviewSerializer(updated_review).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# PUT api/reviews/{idReview}/updatePublished/
class ReviewUpdatePublishedView(APIView):
    def put(self, request, id):
        review = get_object_or_404(Review, id=id)
        user_id = getattr(request, "user_id", None)
        if user_id is not None and review.reviewer_id != user_id:
            return Response(
                {"error": "No tenés permiso sobre esta revisión"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not review.is_published:
            return Response(
                {"error": "Usa el endpoint para borradores"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewUpdateSerializer(review, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            updated_review = serializer.save()
            last_version = review.versions.order_by("version_number").last()
            new_version_number = last_version.version_number + 1 if last_version else 1

            ReviewVersion.objects.create(
                review=updated_review,
                version_number=new_version_number,
                score=updated_review.score,
                opinion=updated_review.opinion,
                chair_comments=updated_review.chair_comments,
            )

        return Response(ReviewSerializer(updated_review).data, status=status.HTTP_200_OK)


# GET /api/article/<int:article_id>/reviews/
class ReviewsArticleView(APIView):
    def get(self, request, article_id):
        reviews = Review.objects.filter(article_id=article_id, is_published=True)
        serializer = PublicReviewSerializer(reviews, many=True)
        return Response({
            "article_id": article_id,
            "count": reviews.count(),
            "reviews": serializer.data,
        })


# GET /api/reviews/{articleId}/{reviewerId}/
class ReviewByReviewerView(APIView):
    def get(self, request, articleId, reviewerId):
        user_id = getattr(request, "user_id", None)
        if user_id is not None and int(reviewerId) != user_id:
            return Response(
                {"error": "No tenés permiso sobre esta revisión"},
                status=status.HTTP_403_FORBIDDEN,
            )
        review = Review.objects.filter(article_id=articleId, reviewer_id=reviewerId).first()
        if not review:
            return Response(
                {"message": "No existe una revisión de ese artículo para este revisor"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = ReviewSerializer(review)
        return Response(serializer.data, status=status.HTTP_200_OK)
