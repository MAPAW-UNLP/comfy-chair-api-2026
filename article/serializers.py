from user.models import User
from rest_framework import serializers
from user.serializers import UserSerializer
from conference_session.models import Session
from .models import Article, ArticleDeletionRequest, ArticleHistory
from conference_session.serializers import SessionSerializer

# --- Serializer para Article ---
class ArticleSerializer(serializers.ModelSerializer):

    # Lectura
    session = SessionSerializer(read_only=True)
    authors = UserSerializer(many=True, read_only=True)
    corresponding_author = UserSerializer(read_only=True)

    # Escritura
    session_id = serializers.PrimaryKeyRelatedField(
        queryset=Session.objects.all(), source='session', write_only=True, required=False, allow_null=True
    )
    authors_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, source='authors', write_only=True
    )
    corresponding_author_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='corresponding_author', write_only=True
    )

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'main_file', 'status', 'type',
            'abstract', 'source_file',
            'authors', 'corresponding_author',
            'authors_ids', 'corresponding_author_id',
            'session', 'session_id'
        ]


class ArticleHistorySerializer(serializers.ModelSerializer):
    verdict = serializers.SerializerMethodField()

    class Meta:
        model = ArticleHistory
        fields = ['event_type', 'created_at', 'verdict']
        read_only_fields = fields

    def get_verdict(self, event):
        if event.event_type != 'final_verdict' or not isinstance(event.metadata, dict):
            return None
        verdict = event.metadata.get('status')
        return verdict if verdict in ('accepted', 'rejected') else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data['verdict'] is None:
            data.pop('verdict')
        return data


# --- Serializer para ArticleDeletionRequest ---
class ArticleDeletionRequestSerializer(serializers.ModelSerializer):
    
    # Lectura
    article = ArticleSerializer(read_only=True)
    
    # Escritura
    article_id = serializers.PrimaryKeyRelatedField(
        queryset=Article.objects.all(), source='article', write_only=True
    )

    class Meta:
        model = ArticleDeletionRequest
        fields = [
            'id', 'article', 'article_id', 'description', 
            'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']
