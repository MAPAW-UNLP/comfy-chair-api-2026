from user.models import User
from rest_framework import serializers
from user.serializers import UserSerializer
from conference_session.models import Session
from .models import Article, ArticleDeletionRequest, ArticleHistory, Source
from conference_session.serializers import SessionSerializer
from .validators import ArticleCompletenessValidator

class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = ['id', 'file_path', 'filename']

class ArticleSerializer(serializers.ModelSerializer):

    # Lectura
    session = SessionSerializer(read_only=True)
    authors = UserSerializer(many=True, read_only=True)
    corresponding_author = UserSerializer(read_only=True)
    
    # NUEVO: Lectura de Sources vinculados al artículo. 
    # Usamos source='source_set' porque es el related_name por defecto de Django.
    sources = SourceSerializer(source='source_set', many=True, read_only=True)

    # Escritura
    # title (campo del modelo) y session_id son los únicos mínimos exigidos,
    # incluso para un borrador.
    session_id = serializers.PrimaryKeyRelatedField(
        queryset=Session.objects.all(), source='session', write_only=True, required=True
    )
    authors_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, source='authors', write_only=True, required=False
    )
    corresponding_author_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source='corresponding_author', write_only=True, required=False
    )
    
    sources = SourceSerializer(source='source_set', many=True, read_only=True)

    class Meta:
        model = Article
        fields = [
            'id', 'title', 'main_file', 'status', 'type',
            'abstract',
            'authors', 'corresponding_author',
            'authors_ids', 'corresponding_author_id',
            'session', 'session_id',
            'sources' # <- Añadido aquí
        ]

    def validate(self, attrs):
        self._validate_logged_user_is_author(attrs)

        # El estado destino nunca lo decide una edición: PATCH no puede
        # cambiar el estado (lo descarta update(), ver más abajo), así que acá
        # se ignora cualquier 'status' recibido en una edición y se usa el de
        # la instancia. Solo en una creación el 'status' enviado es el real.
        if self.instance is not None:
            target_status = self.instance.status
        else:
            target_status = attrs.get('status')
        if target_status == 'draft':
            return attrs

        def valor(campo, default=None):
            if campo in attrs:
                return attrs[campo]
            return getattr(self.instance, campo, default)

        authors = attrs.get('authors')
        if authors is not None:
            authors_count = len(authors)
        elif self.instance is not None:
            authors_count = self.instance.authors.count()
        else:
            authors_count = 0

        request = self.context.get('request')
        if self.instance is None:
            has_sources = bool(request and request.FILES.getlist('sources'))
        else:
            has_sources = self.instance.source_set.exists()

        errors = ArticleCompletenessValidator(
            title=valor('title'),
            abstract=valor('abstract'),
            main_file=valor('main_file'),
            article_type=valor('type'),
            authors_count=authors_count,
            corresponding_author=valor('corresponding_author'),
            has_sources=has_sources,
        ).errors()

        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    def _validate_logged_user_is_author(self, attrs):
        # El usuario logueado debe estar entre los autores: hoy solo lo
        # validaba el front. Solo se chequea cuando authors_ids / corresponding_author_id
        # vienen en el request, para no exigir autores en un borrador mínimo.
        request = self.context.get('request')
        user_id = getattr(request, 'user_id', None) if request else None
        if not user_id:
            return

        authors = attrs.get('authors')
        corresponding_author = attrs.get('corresponding_author')
        # create() siempre manda 'authors_ids' en el multipart, aunque venga
        # vacío (ver ArticleViewSet.create), así que una lista vacía cuenta
        # como "no se especificaron autores", no como "se quiso vaciarlos".
        if not authors and corresponding_author is None:
            return

        author_ids = {user.pk for user in (authors or [])}
        if corresponding_author is not None:
            author_ids.add(corresponding_author.pk)

        if int(user_id) not in author_ids:
            raise serializers.ValidationError({
                'authors_ids': 'El usuario autenticado debe estar entre los autores del artículo.'
            })

    def create(self, validated_data):
        # 1. Extraemos los sources antes de que DRF intente guardarlos
        sources_data = validated_data.pop('sources_data', [])

        # 2. Creamos el artículo (esto también maneja automáticamente las relaciones ManyToMany como authors_ids)
        article = super().create(validated_data)

        # 3. Creamos y vinculamos los Sources
        # Usamos bulk_create para golpear la base de datos una sola vez
        sources_to_create = [
            Source(article=article, **source) for source in sources_data
        ]
        if sources_to_create:
            Source.objects.bulk_create(sources_to_create)

        return article

    def update(self, instance, validated_data):
        # El único camino draft -> reception es el endpoint submit.
        validated_data.pop('status', None)
        return super().update(instance, validated_data)


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

