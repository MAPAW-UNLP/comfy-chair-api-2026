
from rest_framework import serializers
from reviewer.models import  Review, User,Article,Bid, ReviewVersion, ReviewerInvitation
from chair.models import ReviewAssignment
from conference.models import Conference


class ArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Article
        # corregir typo 'abstract'
        fields = ['id', 'title', 'abstract']

class BidSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ['id', 'reviewer', 'article', 'choice']


class BidUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ['choice']  

class AssignmentReviewSerializer(serializers.ModelSerializer):
    article_title = serializers.CharField(source='article.title', read_only=True)
    # Añadimos 'title' para compatibilidad con el frontend que espera ese campo
    title = serializers.CharField(source='article.title', read_only=True)
    reviewed_status = serializers.CharField(source='get_reviewed_display', read_only=True)
    
    class Meta:
        model = ReviewAssignment
        fields = ['id', 'article', 'article_title', 'title', 'reviewed','reviewed_status']

class ReviewerDetailSerializer(serializers.ModelSerializer):
    # Artículos asignados
    assigned_articles = serializers.SerializerMethodField()
    
    # Estado de bidding (si marcó algún choice o no)
    bidding_status = serializers.SerializerMethodField()
    
    # Lista de bids realizados
    bids = serializers.SerializerMethodField()
    
    # Estadísticas de revisiones
    reviews_count = serializers.SerializerMethodField()
  

    class Meta:
        model = User
        fields = [
            'id', 
            'full_name', 
            'email', 
            'affiliation',
            'assigned_articles',
            'bidding_status',
            'bids',
            'reviews_count',
            
        ]

    def get_assigned_articles(self, obj):
        """Obtiene la lista de nombres de artículos asignados"""
        # Devolver información estructurada de las asignaciones para que el
        # frontend tenga access al `id` y al `title` (evita NaN/undefined)
        assigned_articles = ReviewAssignment.objects.filter(
            reviewer=obj, deleted=False
        ).select_related('article')

        # Reutilizamos el serializer local `AssignmentReviewSerializer` que
        # incluye `article_title` (necesario para que el frontend muestre el título)
        return AssignmentReviewSerializer(assigned_articles, many=True).data

    def get_bidding_status(self, obj):
        """Verifica si el revisor ha realizado algún bidding"""
        has_bids = Bid.objects.filter(reviewer=obj).exists()
        return {
            'has_bids': has_bids,
            'total_bids': Bid.objects.filter(reviewer=obj).count(),
            'bids_with_choice': Bid.objects.filter(reviewer=obj).exclude(choice__isnull=True).exclude(choice='').count()
        }

    def get_bids(self, obj):
        """Obtiene todos los bids realizados por el revisor"""
        bids = Bid.objects.filter(reviewer=obj).select_related('article')
        return BidSerializer(bids, many=True).data

    def get_reviews_count(self, obj):
        """Cuenta cuántas revisiones ha completado el revisor"""
        return ReviewAssignment.objects.filter(
            reviewer=obj,
            reviewed=True,  # Solo las completadas
        ).count()


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['id','score','opinion','created_at','updated_at','reviewer','article','is_published']

class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['score','opinion', 'updated_at','is_published']  


class ReviewVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewVersion
        fields = ['id','review','version_number','score','opinion','created_at']


class InvitationConferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conference
        fields = ['id', 'title']


class InvitationUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'full_name']


# Solo lectura. Espera un queryset anotado con with_effective_status()
class ReviewerInvitationSerializer(serializers.ModelSerializer):
    status = serializers.CharField(source='effective_status', read_only=True)
    conference = InvitationConferenceSerializer(read_only=True)
    invited_by = InvitationUserSerializer(read_only=True)

    class Meta:
        model = ReviewerInvitation
        fields = ['id', 'status', 'conference', 'invited_by', 'sent_at', 'expires_at', 'responded_at', 'rejection_reason']
        read_only_fields = fields


class InvitationConferenceDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conference
        fields = ['id', 'title', 'description', 'start_date', 'end_date', 'blind_kind']


# Igual que el ítem del listado, con más datos de la conferencia
class ReviewerInvitationDetailSerializer(ReviewerInvitationSerializer):
    conference = InvitationConferenceDetailSerializer(read_only=True)

    class Meta(ReviewerInvitationSerializer.Meta):
        pass


class RejectInvitationSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=500)