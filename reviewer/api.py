#from datetime import timezone
from django.utils import timezone
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.db.models import Case, Exists, F, OuterRef, Q, Value, When
from reviewer.models import Review, Article, Bid, ReviewVersion, User, ReviewerInvitation, InvitationNotification
from chair.models import ReviewAssignment
from conference.models import Conference
from reviewer.conflicts import AUTHOR_CANNOT_REVIEW_ERROR, is_article_author
from reviewer.serializers import (
    ReviewUpdateSerializer,
    ReviewerDetailSerializer,
    BidSerializer,
    BidUpdateSerializer,
    ReviewSerializer,
    ReviewVersionSerializer,
    PublicReviewSerializer,
    ReviewerAssignmentSerializer,
    ReviewerInvitationSerializer,
    ReviewerInvitationDetailSerializer,
    RejectInvitationSerializer,
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
        if not serializer.is_valid():
            return Response({"error": "Opción de interés inválida"}, status=status.HTTP_400_BAD_REQUEST)
        bid, created = Bid.objects.update_or_create(
            reviewer_id=getattr(request, "user_id", None),
            article=serializer.validated_data["article"],
            defaults={"choice": serializer.validated_data["choice"]},
        )
        return Response(
            BidSerializer(bid).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    

# PUT /api/bidding/{id}
class BiddingUpdateView(APIView):
    def put(self, request, id):
        #Busca el bid con el id, si no lo encuentra retorna 404
        bid = Bid.objects.filter(id=id).first()
        if bid is None:
            return Response({"error": "Bid no encontrado"}, status=status.HTTP_404_NOT_FOUND)
        if bid.reviewer_id != getattr(request, "user_id", None):
            return Response({"error": "No podés modificar un bid ajeno"}, status=status.HTTP_403_FORBIDDEN)
        serializer = BidUpdateSerializer(bid, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# GET /api/bids?reviewerId=123
class ReviewerBidsView(APIView):
    def get(self, request):
        uid = getattr(request, "user_id", None)
        reviewer_id = request.GET.get('reviewerId')
        if reviewer_id is not None and reviewer_id != str(uid):
            return Response({"error": "No podés ver los bids de otro revisor"}, status=status.HTTP_403_FORBIDDEN)
        conference_id = request.GET.get('conference_id')
        session_id = request.GET.get('session_id')
        if any(valor is not None and not valor.isdecimal() for valor in (conference_id, session_id)):
            return Response(
                {"error": "conference_id y session_id deben ser numéricos"}, status=status.HTTP_400_BAD_REQUEST
            )
        bids = Bid.objects.filter(reviewer_id=uid).select_related("article")
        if conference_id:
            bids = bids.filter(article__session__conference_id=conference_id)
        if session_id:
            bids = bids.filter(article__session_id=session_id)
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

NOT_ASSIGNED_ERROR = "No estás asignado para revisar este artículo"


def has_active_assignment(reviewer, article):
    # Una asignación borrada por el chair (deleted=True) no habilita a revisar
    return ReviewAssignment.objects.filter(reviewer=reviewer, article=article, deleted=False).exists()


def review_forbidden_reason(reviewer, article):
    # Motivo por el que el usuario no puede revisar el artículo, o None si puede
    if is_article_author(reviewer, article):
        return AUTHOR_CANNOT_REVIEW_ERROR
    if not has_active_assignment(reviewer, article):
        return NOT_ASSIGNED_ERROR
    return None


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
            reason = review_forbidden_reason(serializer.validated_data['reviewer'], serializer.validated_data['article'])
            if reason:
                return Response({"error": reason}, status=status.HTTP_403_FORBIDDEN)
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


# GET /api/reviews/reviewer/{reviewerId}/?conference_id=<int>
# Devuelve todas las revisiones publicadas de un revisor.
# Con conference_id, solo las de esa conferencia.
class ReviewsByReviewerIdView(APIView):
    def get(self, request, reviewerId):
        try:
            conference_id = _entero_opcional(request.query_params.get("conference_id"), "conference_id")
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
 
        reviews = Review.objects.filter(reviewer_id=reviewerId, is_published=True)
        if conference_id is not None:
            reviews = reviews.filter(article__session__conference_id=conference_id)
 
        # Se mantiene el 404 sin resultados: el front lo trata como lista vacía.
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
        if is_article_author(review.reviewer, review.article):
            return Response({"error": AUTHOR_CANNOT_REVIEW_ERROR}, status=status.HTTP_403_FORBIDDEN)

        # Usar transacción atómica para garantizar consistencia
        with transaction.atomic():
            try:
                assignment = ReviewAssignment.objects.get(
                    reviewer=review.reviewer,
                    article=review.article,
                    deleted=False
                )
                assignment.reviewed = True
                assignment.save()
            except ReviewAssignment.DoesNotExist:
                return Response(
                    {"error": NOT_ASSIGNED_ERROR},
                    status=status.HTTP_403_FORBIDDEN
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
        reason = review_forbidden_reason(review.reviewer, review.article)
        if reason:
            return Response({"error": reason}, status=status.HTTP_403_FORBIDDEN)

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
        reason = review_forbidden_reason(review.reviewer, review.article)
        if reason:
            return Response({"error": reason}, status=status.HTTP_403_FORBIDDEN)

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

def _entero_opcional(valor, nombre):
    """Convierte un parámetro de query a int. None si no vino."""
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except ValueError:
        raise ValueError(f"{nombre} debe ser un número entero")
 
 
def _calcular_estadisticas(items):
    """Cuenta estados y progreso por sesión sobre la lista ya serializada."""
    stats = {
        "total": len(items),
        "published": 0,
        "draft": 0,
        "pending": 0,
        "by_session": [],
    }
    por_sesion = {}
    for item in items:
        stats[item["review_status"]] += 1
        sesion = item["session"]
        if sesion is None:
            # Un artículo sin sesión cuenta en el total pero no tiene fila propia.
            continue
        fila = por_sesion.setdefault(sesion["id"], {
            "session_id": sesion["id"],
            "title": sesion["title"],
            "total": 0,
            "published": 0,
        })
        fila["total"] += 1
        if item["review_status"] == "published":
            fila["published"] += 1
    stats["by_session"] = list(por_sesion.values())
    return stats
 
 
# GET /api/reviewer/assignments/?conference_id=<int>&session_id=<int>
# Devuelve los artículos asignados al revisor logueado, con el estado de su
# review y estadísticas, en un solo request. Ambos filtros son opcionales.
class ReviewerAssignmentsView(APIView):
    def get(self, request):
        # El revisor sale del token, nunca del query ni del body.
        uid = getattr(request, "user_id", None)
        if not uid:
            return Response({"error": "Usuario no autenticado"}, status=status.HTTP_401_UNAUTHORIZED)
 
        try:
            conference_id = _entero_opcional(request.query_params.get("conference_id"), "conference_id")
            session_id = _entero_opcional(request.query_params.get("session_id"), "session_id")
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
 
        asignaciones = (
            ReviewAssignment.objects
            .filter(reviewer_id=uid, deleted=False)
            .select_related("article__session__conference")
            .order_by("article__session_id", "article_id")
        )
        if conference_id is not None:
            asignaciones = asignaciones.filter(article__session__conference_id=conference_id)
        if session_id is not None:
            asignaciones = asignaciones.filter(article__session_id=session_id)
        asignaciones = list(asignaciones)
 
        # Las reviews propias de esos artículos, en una sola consulta.
        # gana la primera por id (igual que ReviewDetailView, que usa .first()).
        reviews = {}
        propias = Review.objects.filter(
            reviewer_id=uid,
            article_id__in=[a.article_id for a in asignaciones],
        ).order_by("id")
        for review in propias:
            reviews.setdefault(review.article_id, review)
 
        items = ReviewerAssignmentSerializer(
            asignaciones, many=True, context={"reviews": reviews}
        ).data
 
        return Response(
            {"results": items, "stats": _calcular_estadisticas(items)},
            status=status.HTTP_200_OK,
        )
 

# GET /api/reviewer/invitations/?status=<pending|accepted|rejected|expired>
# Devuelve las invitaciones a comités de revisión del usuario logueado
class ReviewerInvitationsView(APIView):
    VALID_STATUSES = {'pending', 'accepted', 'rejected', 'expired'}

    def get(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        status_filter = request.query_params.get('status')
        if status_filter is not None and status_filter not in self.VALID_STATUSES:
            return Response(
                {'error': f"Estado inválido. Valores posibles: {', '.join(sorted(self.VALID_STATUSES))}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        invitations = (
            ReviewerInvitation.objects
            .filter(reviewer_id=user_id)
            .with_effective_status()
            .select_related('conference', 'invited_by')
        )
        if status_filter:
            invitations = invitations.filter(effective_status=status_filter)

        # Pendientes primero (por fecha límite más próxima, las que no vencen al final);
        # después el resto por fecha de respuesta descendente
        invitations = invitations.annotate(
            is_pending=Case(When(effective_status='pending', then=Value(0)), default=Value(1)),
            pending_expires_at=Case(When(effective_status='pending', then=F('expires_at'))),
        ).order_by(
            'is_pending',
            F('pending_expires_at').asc(nulls_last=True),
            F('responded_at').desc(nulls_last=True),
            '-sent_at',
        )

        serializer = ReviewerInvitationSerializer(invitations, many=True)
        return Response({'results': serializer.data}, status=status.HTTP_200_OK)


# GET /api/reviewer/invitations/{id}/
# Devuelve el detalle de una invitación del usuario logueado
class ReviewerInvitationDetailView(APIView):
    def get(self, request, id):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        invitation = (
            ReviewerInvitation.objects
            .with_effective_status()
            .select_related('conference', 'invited_by')
            .filter(id=id)
            .first()
        )
        if invitation is None:
            return Response({'error': 'Invitación no encontrada'}, status=status.HTTP_404_NOT_FOUND)
        if invitation.reviewer_id != user_id:
            return Response({'error': 'No tenés acceso a esta invitación'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReviewerInvitationDetailSerializer(invitation)
        return Response(serializer.data, status=status.HTTP_200_OK)


# Lógica común para aceptar/rechazar una invitación
class RespondInvitationView(APIView):
    new_status = None

    def apply(self, invitation, data):
        pass

    def respond(self, request, id, data=None):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        # select_for_update bloquea la fila hasta el fin de la transacción:
        # dos requests simultáneos no pueden responder la misma invitación
        with transaction.atomic():
            invitation = ReviewerInvitation.objects.select_for_update().filter(id=id).first()
            if invitation is None:
                return Response({'error': 'Invitación no encontrada'}, status=status.HTTP_404_NOT_FOUND)
            if invitation.reviewer_id != user_id:
                return Response({'error': 'No tenés permiso sobre esta invitación'}, status=status.HTTP_403_FORBIDDEN)
            if invitation.status != 'pending':
                return Response({'error': 'Esta invitación ya fue respondida'}, status=status.HTTP_409_CONFLICT)
            if invitation.is_expired():
                return Response({'error': 'La invitación está vencida'}, status=status.HTTP_400_BAD_REQUEST)

            invitation.status = self.new_status
            invitation.responded_at = timezone.now()
            self.apply(invitation, data)
            invitation.save()

        invitation = (
            ReviewerInvitation.objects
            .with_effective_status()
            .select_related('conference', 'invited_by')
            .get(id=id)
        )
        return Response(ReviewerInvitationDetailSerializer(invitation).data, status=status.HTTP_200_OK)


# POST /api/reviewer/invitations/{id}/accept/
class AcceptInvitationView(RespondInvitationView):
    new_status = 'accepted'

    def post(self, request, id):
        return self.respond(request, id)


# POST /api/reviewer/invitations/{id}/reject/   body: { "reason": "..." } (opcional)
class RejectInvitationView(RespondInvitationView):
    new_status = 'rejected'

    def apply(self, invitation, data):
        invitation.rejection_reason = data.get('reason') or ''

    def post(self, request, id):
        serializer = RejectInvitationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'El motivo debe ser un texto de hasta 500 caracteres'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return self.respond(request, id, serializer.validated_data)


# GET /api/reviewer/conferences/
# Conferencias donde el usuario es revisor: invitación aceptada o al menos una asignación no borrada
class ReviewerConferencesView(APIView):
    def get(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        has_assignments = Exists(ReviewAssignment.objects.filter(
            reviewer_id=user_id,
            deleted=False,
            article__session__conference=OuterRef('pk'),
        ))
        has_accepted_invitation = Exists(ReviewerInvitation.objects.filter(
            reviewer_id=user_id,
            status='accepted',
            conference=OuterRef('pk'),
        ))
        # Exists evita filas duplicadas, por eso no hace falta distinct()
        conferences = (
            Conference.objects
            .annotate(has_assignments=has_assignments, has_accepted_invitation=has_accepted_invitation)
            .filter(Q(has_assignments=True) | Q(has_accepted_invitation=True))
            .order_by('title')
        )

        results = [
            {'id': c.id, 'title': c.title, 'has_assignments': c.has_assignments}
            for c in conferences
        ]
        return Response({'results': results}, status=status.HTTP_200_OK)


# GET /api/reviewer/invitation-notifications/
# Qué notificaciones del usuario son de invitaciones, a qué invitación apuntan y su estado
# (el front muestra Aceptar/Rechazar en las pendientes)
class InvitationNotificationsView(APIView):
    def get(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        notifications = (
            InvitationNotification.objects
            .filter(user_id=user_id)
            .select_related('invitation__conference')
            .order_by('-created_at')
        )
        results = [
            {
                'notification': n.id,
                'invitation': n.invitation_id,
                'status': 'expired' if n.invitation.is_expired() else n.invitation.status,
                'conference_title': n.invitation.conference.title,
            }
            for n in notifications
        ]
        return Response({'results': results}, status=status.HTTP_200_OK)


# GET /api/reviewer/articles/{article_id}/assignment/
# Indica si el usuario logueado puede revisar el artículo (tiene una asignación vigente)
class ReviewerArticleAssignmentView(APIView):
    def get(self, request, article_id):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return Response({'error': 'Usuario no autenticado'}, status=status.HTTP_401_UNAUTHORIZED)

        if not Article.objects.filter(id=article_id).exists():
            return Response({'error': 'Artículo no encontrado'}, status=status.HTTP_404_NOT_FOUND)
        reason = review_forbidden_reason(user_id, article_id)
        if reason:
            return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)
        return Response({'assigned': True}, status=status.HTTP_200_OK)
