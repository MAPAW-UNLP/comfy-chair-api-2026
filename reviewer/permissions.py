from django.shortcuts import get_object_or_404
from rest_framework.permissions import BasePermission

from article.models import Article
from article.permissions import can_read_article
from reviewer.models import Review
from user.access import authenticated_user, require_reviewer


class OwnReviewPermission(BasePermission):
    message = 'No tenés permiso sobre esta revisión.'

    def has_permission(self, request, view):
        user = authenticated_user(request)
        kwargs = view.kwargs
        if 'reviewerId' in kwargs and kwargs['reviewerId'] != user.pk:
            return False
        review_id = kwargs.get('idReview') or kwargs.get('id')
        if review_id:
            review = get_object_or_404(Review, pk=review_id)
            return review.reviewer_id == user.pk
        if 'articleId' in kwargs:
            article = get_object_or_404(Article, pk=kwargs['articleId'])
            require_reviewer(request, article.session.conference_id if article.session_id else None)
        return True


class ArticleReviewsPermission(BasePermission):
    message = 'No tenés permiso para consultar las revisiones de este artículo.'

    def has_permission(self, request, view):
        article = get_object_or_404(Article, pk=view.kwargs['article_id'])
        return can_read_article(authenticated_user(request), article)


