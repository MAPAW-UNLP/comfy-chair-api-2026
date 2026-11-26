import zipfile
import io
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse, Http404, HttpResponse
from article.models import Article, ArticleHistory, Source, ArticleFile
from user.models import User
from conference_session.models import (
    Session,
    are_submission_periods_open,
    get_submission_period,
)
from .serializers import ArticleSerializer, ArticleDeletionRequestSerializer, ArticleHistorySerializer
from .permissions import IsArticleAuthor
from .validators import ArticleCompletenessValidator
from reviewer.conflicts import is_article_author
from pathlib import Path

# Acciones de detalle que solo puede ver/tocar un autor del artículo cuando
# este está en borrador (ver IsArticleAuthor y get_queryset).
_AUTHOR_ONLY_ACTIONS = (
    "retrieve",
    "update",
    "partial_update",
    "submit",
    "upload_camera_ready_file",
)


def _delete_stored_files(files):
    # files: lista de (storage, name). Borra los archivos del disco/storage.
    for storage, name in files:
        if name:
            storage.delete(name)


def _iso_utc_or_none(value):
    return value.isoformat().replace('+00:00', 'Z') if value is not None else None


def _submission_period_error(*sessions):
    if any(session is None for session in sessions):
        return Response(
            {'errors': {'session_id': ['El artículo no tiene una sesión válida.']}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not are_submission_periods_open(*sessions):
        return Response(
            {
                'code': 'submission_period_closed',
                'detail': 'El período de submissions de esta sesión está cerrado.',
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _resolve_user(request):
    user = getattr(request, "auth_user", None)
    if user is not None:
        return user
    user_id = getattr(request, "user_id", None)
    return User.objects.filter(pk=user_id).first() if user_id else None


def _visible_articles(queryset, request):
    # Un borrador ajeno no se muestra; el propio borrador del usuario
    # autenticado sí, mezclado con el resto, igual que un artículo en
    # cualquier otro estado.
    user = _resolve_user(request)
    if user is None:
        return queryset.exclude(status="draft")
    return queryset.filter(
        Q(authors=user) | Q(corresponding_author=user) | ~Q(status="draft")
    ).distinct()


# --- Endpoints para el modelo Article ---
class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    @action(detail=False, methods=['get'], url_path='submission-status')
    def submission_status(self, request):
        raw_session_id = request.query_params.get('session_id')
        if (
            not raw_session_id
            or not raw_session_id.isascii()
            or not raw_session_id.isdecimal()
            or len(raw_session_id) > 19
        ):
            return Response(
                {'session_id': ['Se requiere un ID de sesión válido.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session_id = int(raw_session_id)
        if not 1 <= session_id <= 9223372036854775807:
            return Response(
                {'session_id': ['Se requiere un ID de sesión válido.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = get_object_or_404(
            Session.objects.select_related('conference'), pk=session_id
        )
        period = get_submission_period(session)
        return Response({
            'session_id': session.pk,
            'submission_open': period.is_open(),
            'submission_start': _iso_utc_or_none(period.start),
            # This is the exclusive boundary, not the last permitted instant.
            'submission_deadline': _iso_utc_or_none(period.end_exclusive),
        })

    def get_permissions(self):
        if self.action in _AUTHOR_ONLY_ACTIONS:
            return [IsArticleAuthor()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()

        if self.action == "list" or self.action in _AUTHOR_ONLY_ACTIONS:
            # Listado general y acciones de detalle: un borrador ajeno no se
            # ve (404/no aparece); el borrador propio del usuario autenticado
            # sí, mezclado con el resto, igual que cualquier otro artículo.
            return _visible_articles(queryset, self.request)

        return queryset

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        article = self.get_object()
        events = article.history_events.order_by("created_at", "id")
        return Response(ArticleHistorySerializer(events, many=True).data)

    # ------------------------------------------------------------
    # GRUPO 1 - Endpoint para el alta de un articulo
    # ------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        if hasattr(request.data, "_mutable"):
            request.data._mutable = True

        authors_ids = (
            request.data.getlist("authors_ids")
            if hasattr(request.data, "getlist")
            else request.data.get("authors_ids", [])
        )
        authors_ids = (
            authors_ids
            if isinstance(authors_ids, list)
            else ([authors_ids] if authors_ids else [])
        )

        if hasattr(request.data, "setlist"):
            request.data.setlist("authors_ids", authors_ids)
        else:
            request.data["authors_ids"] = authors_ids

        if hasattr(request.data, "_mutable"):
            request.data._mutable = False

        serializer = self.get_serializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "errors": serializer.errors,
                    "data_received": "Data contains files, omitted for debug",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        period_error = _submission_period_error(serializer.validated_data['session'])
        if period_error is not None:
            return period_error

        with transaction.atomic():
            self.perform_create(serializer)
            created_article = serializer.instance

            author = created_article.corresponding_author
            ArticleHistory.objects.create(
                article=created_article,
                event_type=(
                    "draft_created"
                    if created_article.status == "draft"
                    else "submitted"
                ),
                created_by_id=(
                    author.id if author else getattr(request, "user_id", None)
                ),
            )

            source_files = request.FILES.getlist("sources")

            for file in source_files:
                Source.objects.create(
                    article=created_article, file_path=file, filename=file.name
                )

        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)

        if not serializer.is_valid():
            return Response(
                {"errors": serializer.errors, "data_received": request.data},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_session = serializer.validated_data.get('session', instance.session)
        period_error = _submission_period_error(instance.session, target_session)
        if period_error is not None:
            return period_error

        with transaction.atomic():
            self.perform_update(serializer)

            # Mismo manejo que create(): agrega los archivos fuente nuevos
            # que vengan en el request, sin tocar los que ya existían.
            source_files = request.FILES.getlist("sources")
            for file in source_files:
                Source.objects.create(
                    article=instance, file_path=file, filename=file.name
                )

        return Response(self.get_serializer(instance).data)

    # ------------------------------------------------------------
    # Endpoint para enviar un borrador: draft -> reception
    # ------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        article = self.get_object()

        if article.status != "draft":
            return Response(
                {"detail": "El artículo no está en borrador."},
                status=status.HTTP_409_CONFLICT,
            )

        period_error = _submission_period_error(article.session)
        if period_error is not None:
            return period_error

        errors = ArticleCompletenessValidator(
            title=article.title,
            abstract=article.abstract,
            main_file=article.main_file,
            article_type=article.type,
            authors_count=article.authors.count(),
            corresponding_author=article.corresponding_author,
            has_sources=article.source_set.exists(),
        ).errors()
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        article._notify_submitted = True
        article.status = "reception"
        article.save()

        ArticleHistory.objects.create(
            article=article,
            event_type="submitted",
            created_by=getattr(request, "auth_user", None),
        )

        return Response(self.get_serializer(article).data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------
    # GRUPO 1 - Endpoint para descargar el archivo principal
    # ------------------------------------------------------------
    @action(detail=True, methods=["get"])
    def download_main(self, request, pk=None):
        article = self.get_object()
        if not article.main_file:
            raise Http404("Este artículo no tiene archivo principal.")
        response = FileResponse(article.main_file.open("rb"), as_attachment=True)
        response["Content-Disposition"] = (
            f'attachment; filename="{article.main_file.name.split("/")[-1]}"'
        )
        return response

    # ------------------------------------------------------------
    # GRUPO 1 - Endpoint para descargar el archivo de fuentes
    # ------------------------------------------------------------
    @action(detail=True, methods=["get"])
    def download_source(self, request, pk=None):
        article = self.get_object()

        # Obtenemos todos los sources relacionados al artículo
        sources = article.source_set.all()

        if not sources.exists():
            raise Http404("Este artículo no tiene archivos fuente.")

        # Creamos un buffer en memoria para el archivo ZIP
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for source in sources:
                if source.file_path:
                    # Abrimos cada archivo iterado y lo escribimos dentro del ZIP
                    # Usamos source.filename para darle el nombre original dentro del ZIP
                    with source.file_path.open("rb") as f:
                        zip_file.writestr(source.filename, f.read())

        # Volvemos el puntero del buffer al inicio antes de enviarlo
        zip_buffer.seek(0)

        # Enviamos la respuesta indicando que es un archivo ZIP
        response = HttpResponse(zip_buffer, content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="sources_article_{article.id}.zip"'
        )

        return response

    # endpoint hecho por grupo 3 para obtener articulos en base al id de una sesion dada
    @action(
        detail=False,
        methods=["get"],
        url_path="getArticlesBySessionId/(?P<session_id>[^/.]+)",
    )
    def getArticlesBySessionId(self, request, session_id=None):
        articles = _visible_articles(
            Article.objects.filter(session_id=session_id), request
        )
        serializer = ArticleSerializer(articles, many=True)
        return Response(serializer.data)

    # endpoint hecho por el grupo 3 para obtener articulos en base al id de una conferencia
    @action(
        detail=False,
        methods=["get"],
        url_path="getArticlesByConferenceId/(?P<conference_id>[^/.]+)",
    )
    def getArticlesByConferenceId(self, request, conference_id=None):
        articles = _visible_articles(
            Article.objects.filter(session__conference=conference_id), request
        )
        serializer = ArticleSerializer(articles, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="camera_ready")
    def upload_camera_ready_file(self, request, pk=None):
        article: Article = self.get_object()
        is_author: bool = is_article_author(request.auth_user, article)
        if not is_author:
            return Response(
                {"detail": "El usuario no es autor del artículo"}, status=403
            )
        if article.status != "accepted":
            return Response(
                {"detail": "El artículo tiene que estar aceptado"}, status=409
            )
        conference = article.session.conference
        deadline = conference.camera_ready_deadline
        if not deadline or date.today() > deadline:
            return Response(
                {"detail": "El deadline es inválido"},
                status=400,
            )
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "Falta el archivo"}, status=400)
        file_suffix = Path(file.name).suffix.lower().lstrip(".")
        if file_suffix not in conference.allowed_extensions:
            return Response(
                {"detail": "No se acepta la extensión del archivo"}, status=400
            )
        with transaction.atomic():
            # --- Reemplazo del camera-ready anterior ---
            # Cuando se pueda subir más de un archivo camera_ready, sacar todo
            # este bloque (hasta el on_commit inclusive).
            camera_ready_file = article.files.filter(is_camera_ready=True)
            old_files = [(f.file.storage, f.file.name) for f in camera_ready_file]
            camera_ready_file.delete()
            # El archivo físico se borra recién cuando la transacción confirma,
            # así un rollback no deja una fila apuntando a un archivo inexistente.
            transaction.on_commit(lambda: _delete_stored_files(old_files))
            # --- Fin del reemplazo ---
            article_file: ArticleFile = ArticleFile.objects.create(
                article=article,
                file=file,
                filename=file.name,
                is_camera_ready=True,
                uploaded_by=request.auth_user,
            )
            ArticleHistory.objects.create(
                event_type="camera_ready_uploaded",
                metadata={"filename": file.name},
                article=article,
                created_by=request.auth_user,
            )
        serializer = ArticleFileSerializer(article_file, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)
