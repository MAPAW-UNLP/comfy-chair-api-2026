from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from conference_session.models import Session
from conference_session.permissions import IsAdminOrConferenceChair
from conference_session.serializers import SessionDetailSerializer

from article.models import Article
from article.serializers import ArticleSerializer
from user.access import IsConferenceChair
from article.views import _visible_articles


class SessionViewSet(viewsets.ModelViewSet):
    queryset = Session.objects.select_related('conference').prefetch_related('chairs', 'conference__chairs')
    serializer_class = SessionDetailSerializer
    permission_classes = [IsAdminOrConferenceChair]

    def get_permissions(self):
        if self.action == 'articles':
            self.chair_session_pk = True
            return [IsConferenceChair()]
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        conference_id = self.request.query_params.get('conference_id')

        if conference_id:
            queryset = queryset.filter(conference_id=conference_id)

        return queryset

    @action(
        detail=True,
        methods=['get'],
        url_path='articles'
    )
    def articles(self, request, pk=None):
        session = self.get_object()

        articles = _visible_articles(Article.objects.filter(session=session), request)

        serializer = ArticleSerializer(articles, many=True)

        return Response(serializer.data)
