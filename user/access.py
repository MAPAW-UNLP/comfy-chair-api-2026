"""Conference roles derived from database relations, never from client identities."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from conference_session.models import Session
from reviewer.models import ReviewerInvitation
from user.models import User


def authenticated_user(request):
    user = getattr(request, 'auth_user', None)
    if user is None:
        user = User.objects.filter(
            pk=getattr(request, 'user_id', None), is_active=True, deleted=False,
        ).first()
    if user is None:
        raise NotAuthenticated('Usuario no autenticado.')
    request.auth_user = user
    return user


def chair_conferences(user):
    return Conference.objects.filter(Q(chairs=user) | Q(session__chairs=user)).distinct()


def reviewer_conferences(user):
    assignments = ReviewAssignment.objects.filter(reviewer=user, deleted=False)
    invitations = ReviewerInvitation.objects.filter(reviewer=user, status='accepted')
    return Conference.objects.filter(
        Q(pk__in=assignments.values('article__session__conference_id')) |
        Q(pk__in=invitations.values('conference_id'))
    ).distinct()


def author_articles(user):
    return Article.objects.filter(Q(authors=user) | Q(corresponding_author=user)).distinct()


def require_chair(request, conference_id):
    user = authenticated_user(request)
    if user.role != 'admin' and not chair_conferences(user).filter(pk=conference_id).exists():
        raise PermissionDenied('No tenés rol de chair en esta conferencia.')


def require_reviewer(request, conference_id):
    user = authenticated_user(request)
    if user.role != 'admin' and not reviewer_conferences(user).filter(pk=conference_id).exists():
        raise PermissionDenied('No tenés rol de revisor en esta conferencia.')


def resource_id(value):
    if isinstance(value, bool) or not str(value).isdecimal():
        raise ValidationError({'detail': 'El identificador debe ser numérico.'})
    return int(value)


class IsConferenceChair(BasePermission):
    """Chair endpoints declare their resource through URL/body ids."""
    def has_permission(self, request, view):
        user = authenticated_user(request)
        kwargs = view.kwargs
        session_id = kwargs.get('session_id') or request.query_params.get('session_id')
        if getattr(view, 'chair_session_pk', False):
            session_id = kwargs.get('pk')
        # A body field must never replace the resource targeted by the URL.
        article_id = kwargs.get('article_id')
        if getattr(view, 'chair_article_body', False):
            article_id = request.data.get('article')
            session_id = None
        if article_id:
            article = get_object_or_404(Article, pk=resource_id(article_id))
            require_chair(request, article.session.conference_id if article.session_id else None)
        elif session_id:
            session = get_object_or_404(Session, pk=resource_id(session_id))
            require_chair(request, session.conference_id)
        elif user.role != 'admin' and not chair_conferences(user).exists():
            raise PermissionDenied('No tenés rol de chair.')
        return True


class IsConferenceReviewer(BasePermission):
    def has_permission(self, request, view):
        user = authenticated_user(request)
        conference_id = request.query_params.get('conference_id')
        session_id = view.kwargs.get('session_id') or request.query_params.get('session_id')
        article_id = request.data.get('article')
        for field in ('conference_id', 'session_id'):
            value = request.query_params.get(field)
            if value is not None and not value.isdecimal():
                # Empty conference_id is an existing optional history filter.
                if value == '' and field == 'conference_id' and 'reviewerId' in view.kwargs:
                    continue
                raise ValidationError({'error': 'conference_id y session_id deben ser numéricos'})
        if conference_id:
            require_reviewer(request, conference_id)
        if session_id:
            session = get_object_or_404(Session, pk=session_id)
            require_reviewer(request, session.conference_id)
        if article_id:
            article = get_object_or_404(Article, pk=article_id)
            require_reviewer(request, article.session.conference_id if article.session_id else None)
        if not conference_id and not session_id and not article_id:
            if user.role != 'admin' and not (
                reviewer_conferences(user).exists() or
                ReviewAssignment.objects.filter(reviewer=user, deleted=False).exists()
            ):
                raise PermissionDenied('No tenés rol de revisor.')
        return True
