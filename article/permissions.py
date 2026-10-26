from rest_framework.permissions import BasePermission
from rest_framework.exceptions import NotAuthenticated
from user.models import User
from reviewer.conflicts import is_article_author


class IsArticleAuthor(BasePermission):
    """Solo los autores (authors o corresponding_author) de un BORRADOR pueden
    verlo, editarlo o enviarlo. Un artículo que ya no es draft sigue visible/
    editable para cualquier autenticado, como era antes de este feature (lo
    usan chairs y revisores vía GET /api/article/{id}/ para ver el detalle de
    artículos ya enviados). Reusa is_article_author (reviewer/conflicts.py)
    para no duplicar la definición de "quién es autor" de un artículo."""

    message = "No sos autor de este artículo."

    def _get_authenticated_user(self, request):
        # request.user_id lo setea JWTAuthenticationMiddleware a partir del
        # token decodificado: es el usuario real, no un valor enviado por el cliente.
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return None
        return User.objects.filter(pk=user_id).first()

    def has_permission(self, request, view):
        user = getattr(request, 'auth_user', None) or self._get_authenticated_user(request)
        if user is None:
            raise NotAuthenticated("Usuario no autenticado.")
        # cachear para no repetir la consulta en has_object_permission
        request.auth_user = user
        return True

    def has_object_permission(self, request, view, obj):
        if obj.status != 'draft':
            return True
        user = getattr(request, 'auth_user', None) or self._get_authenticated_user(request)
        if user is None:
            raise NotAuthenticated("Usuario no autenticado.")
        return is_article_author(user, obj)
