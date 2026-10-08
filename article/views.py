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
        # 1. Hacemos una copia mutable de los datos recibidos (soportando dict y QueryDict)
        if hasattr(request.data, 'copy'):
            data = request.data.copy()
        else:
            import copy
            data = copy.deepcopy(request.data)

        # 2. Extraemos los authors_ids de forma segura
        # Si es un FormData tendrá getlist(), si es JSON usamos get() estándar
        if hasattr(request.data, 'getlist'):
            authors_ids = request.data.getlist('authors_ids')
        else:
            authors_ids = request.data.get('authors_ids', [])

        # 3. Forzamos a que siempre sea una lista
        if not isinstance(authors_ids, list):
            authors_ids = [authors_ids] if authors_ids else []

        # 4. Lo reasignamos al objeto data
        if hasattr(data, 'setlist'):
            data.setlist('authors_ids', authors_ids)
        else:
            data['authors_ids'] = authors_ids

        # 5. Pasamos los datos corregidos al serializador
        serializer = self.get_serializer(data=data)
        
        if not serializer.is_valid():
                    return Response({
                        'errors': serializer.errors,
                        'data_received': request.data
                    }, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            # 1. Guarda el Article y su main_file
            self.perform_create(serializer)
            created_article = serializer.instance
            
            # 2. Registra el historial
            author = created_article.corresponding_author
            ArticleHistory.objects.create(
                article=created_article,
                event_type='submitted',
                created_by_id=author.id,
            )

            # 3. EXTRAE Y GUARDA LOS SOURCES FÍSICOS
            # Usamos getlist() para obtener todos los archivos enviados bajo la clave 'sources'
            source_files = request.FILES.getlist('sources')
            
            for file in source_files:
                Source.objects.create(
                    article=created_article,
                    file_path=file,       # Pasamos el archivo directamente
                    filename=file.name    # Extraemos el nombre original del archivo
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
   # @action(detail=True, methods=['get'])
    #def download_source(self, request, pk=None):
   #     article = self.get_object()
   #     if not article.source_file:
  #          raise Http404("Este artículo no tiene archivo fuente.")
  #      response = FileResponse(article.source_file.open('rb'), as_attachment=True)
   #     response['Content-Disposition'] = f'attachment; filename="{article.source_file.name.split("/")[-1]}"'
   #     return response
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
