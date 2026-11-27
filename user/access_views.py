from rest_framework.response import Response
from rest_framework.views import APIView

from chair.models import ReviewAssignment
from conference_session.models import Session
from reviewer.models import ReviewerInvitation
from .access import authenticated_user, author_articles, chair_conferences, reviewer_conferences


class MyAccessAPI(APIView):
    def get(self, request):
        user = authenticated_user(request)
        chairs = chair_conferences(user)
        authored = author_articles(user)
        return Response({
            'chair_conference_ids': list(chairs.values_list('pk', flat=True)),
            'chair_session_ids': list(Session.objects.filter(conference__in=chairs).values_list('pk', flat=True)),
            'reviewer_conference_ids': list(reviewer_conferences(user).values_list('pk', flat=True)),
            'author_conference_ids': list(authored.exclude(session=None).values_list('session__conference_id', flat=True).distinct()),
            'author_article_ids': list(authored.values_list('pk', flat=True)),
            'reviewer_article_ids': list(ReviewAssignment.objects.filter(reviewer=user, deleted=False).values_list('article_id', flat=True)),
            'invitation_ids': list(ReviewerInvitation.objects.filter(reviewer=user).values_list('pk', flat=True)),
        })
