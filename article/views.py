from urllib import request
import zipfile
import io
from rest_framework import status
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse, Http404, HttpResponse
from article.models import Article, ArticleDeletionRequest, ArticleHistory, Source
from .serializers import ArticleSerializer, ArticleDeletionRequestSerializer, ArticleHistorySerializer

# --- Endpoints para el modelo Article ---
class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        article = self.get_object()
        events = article.history_events.order_by('created_at', 'id')
        return Response(ArticleHistorySerializer(events, many=True).data)
    
    #------------------------------------------------------------
    # GRUPO 1 - Endpoint para el alta de un articulo
    #------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        if hasattr(request.data, '_mutable'):
            request.data._mutable = True

        authors_ids = request.data.getlist('authors_ids') if hasattr(request.data, 'getlist') else request.data.get('authors_ids', [])
        authors_ids = authors_ids if isinstance(authors_ids, list) else ([authors_ids] if authors_ids else [])
        
        if hasattr(request.data, 'setlist'):
            request.data.setlist('authors_ids', authors_ids)
        else:
            request.data['authors_ids'] = authors_ids

        if hasattr(request.data, '_mutable'):
            request.data._mutable = False

        serializer = self.get_serializer(data=request.data)
        
        if not serializer.is_valid():
            return Response({
                'errors': serializer.errors,
                'data_received': "Data contains files, omitted for debug"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        with transaction.atomic():
            self.perform_create(serializer)
            created_article = serializer.instance
            
            author = created_article.corresponding_author
            ArticleHistory.objects.create(
                article=created_article,
                event_type='submitted',
                created_by_id=author.id,
            )

            source_files = request.FILES.getlist('sources')
            
            for file in source_files:
                Source.objects.create(
                    article=created_article,
                    file_path=file,
                    filename=file.name
                )

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        
        if not serializer.is_valid():
            return Response({
                'errors': serializer.errors,
                'data_received': request.data
            }, status=status.HTTP_400_BAD_REQUEST)

        self.perform_update(serializer)
        return Response(serializer.data)
    
    #------------------------------------------------------------
    # GRUPO 1 - Endpoint para descargar el archivo principal
    #------------------------------------------------------------
    @action(detail=True, methods=['get'])
    def download_main(self, request, pk=None):
        article = self.get_object()
        if not article.main_file:
            raise Http404("Este artículo no tiene archivo principal.")
        response = FileResponse(article.main_file.open('rb'), as_attachment=True)
        response['Content-Disposition'] = f'attachment; filename="{article.main_file.name.split("/")[-1]}"'
        return response

    #------------------------------------------------------------
    # GRUPO 1 - Endpoint para descargar el archivo de fuentes
    #------------------------------------------------------------
    @action(detail=True, methods=['get'])
    def download_source(self, request, pk=None):
        article = self.get_object()
        
        # Obtenemos todos los sources relacionados al artículo
        sources = article.source_set.all()
        
        if not sources.exists():
            raise Http404("Este artículo no tiene archivos fuente.")

        # Creamos un buffer en memoria para el archivo ZIP
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for source in sources:
                if source.file_path:
                    # Abrimos cada archivo iterado y lo escribimos dentro del ZIP
                    # Usamos source.filename para darle el nombre original dentro del ZIP
                    with source.file_path.open('rb') as f:
                        zip_file.writestr(source.filename, f.read())
        
        # Volvemos el puntero del buffer al inicio antes de enviarlo
        zip_buffer.seek(0)
        
        # Enviamos la respuesta indicando que es un archivo ZIP
        response = HttpResponse(zip_buffer, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="sources_article_{article.id}.zip"'
        
        return response
    
    # endpoint hecho por grupo 3 para obtener articulos en base al id de una sesion dada
    @action(detail=False, methods=['get'], url_path='getArticlesBySessionId/(?P<session_id>[^/.]+)')
    def getArticlesBySessionId(self, request, session_id=None):
        articles = Article.objects.filter(session_id=session_id)
        serializer = ArticleSerializer(articles, many=True)
        return Response(serializer.data)
    
    # endpoint hecho por el grupo 3 para obtener articulos en base al id de una conferencia
    @action(detail=False, methods=['get'], url_path='getArticlesByConferenceId/(?P<conference_id>[^/.]+)')
    def getArticlesByConferenceId(self, request, conference_id=None):
        articles = Article.objects.filter(session__conference=conference_id)
        serializer = ArticleSerializer(articles, many=True)
        return Response(serializer.data)
