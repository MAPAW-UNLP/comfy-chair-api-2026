from rest_framework import status
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse, Http404
from article.models import Article, ArticleDeletionRequest
from conference.periods import is_period_open
from .serializers import ArticleSerializer, ArticleDeletionRequestSerializer

# --- Endpoints para el modelo Article ---
class ArticleViewSet(viewsets.ModelViewSet):

    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    def _submission_is_open(self, session):
        return session is None or is_period_open(session, 'submission')
    
    #------------------------------------------------------------
    # GRUPO 1 - Endpoint para el alta de un articulo
    #------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'errors': serializer.errors,
                'data_received': request.data
            }, status=status.HTTP_400_BAD_REQUEST)

        session = serializer.validated_data.get('session')
        if not self._submission_is_open(session):
            return Response(
                {'error': 'El periodo de envío de artículos está cerrado.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        self.perform_create(serializer)
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

        new_session = serializer.validated_data.get('session', instance.session)
        sessions = {session for session in (instance.session, new_session) if session is not None}
        if any(not self._submission_is_open(session) for session in sessions):
            return Response(
                {'error': 'El periodo de envío de artículos está cerrado.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
        if not article.source_file:
            raise Http404("Este artículo no tiene archivo fuente.")
        response = FileResponse(article.source_file.open('rb'), as_attachment=True)
        response['Content-Disposition'] = f'attachment; filename="{article.source_file.name.split("/")[-1]}"'
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
