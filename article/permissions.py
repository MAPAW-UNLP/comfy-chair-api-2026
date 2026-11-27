from rest_framework.permissions import BasePermission, SAFE_METHODS
from user.access import authenticated_user, chair_conferences, reviewer_conferences
from reviewer.conflicts import is_article_author


class IsArticleAuthor(BasePermission):
    """Author operations require authorship; shared reads require a related role."""

    message = "No sos autor de este artículo."

    def has_permission(self, request, view):
        authenticated_user(request)
        return True

    def has_object_permission(self, request, view, obj):
        user = authenticated_user(request)
        if user.role == 'admin':
            return True
        if request.method in SAFE_METHODS and view.action != 'history':
            return can_read_article(user, obj)
        return is_article_author(user, obj)


def can_read_article(user, article):
    if user.role == 'admin' or is_article_author(user, article):
        return True
    if article.status == 'draft' or not article.session_id:
        return False
    conference_id = article.session.conference_id
    return (
        chair_conferences(user).filter(pk=conference_id).exists() or
        reviewer_conferences(user).filter(pk=conference_id).exists()
    )
