from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from user.models import User
from conference.models import Conference


class IsAdminOrConferenceChair(BasePermission):

    message = "No tenés permisos para administrar las sesiones de esta conferencia."

    def _get_authenticated_user(self, request):
        # request.user_id lo setea JWTAuthenticationMiddleware a partir del
        # token decodificado: es el usuario real, no un valor enviado por el cliente.
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return None
        return User.objects.filter(pk=user_id).first()

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        user = self._get_authenticated_user(request)
        if user is None:
            raise NotAuthenticated("Usuario no autenticado.")

        # cachear para no repetir la consulta en has_object_permission
        request.auth_user = user

        if user.role == 'admin':
            return True

        if view.action == 'create':
            conference_id = request.data.get('conference_id') or request.data.get('conference')
            if not conference_id:
                raise PermissionDenied(self.message)
            if not Conference.objects.filter(pk=conference_id, chairs=user).exists():
                raise PermissionDenied(self.message)
            return True

        # update / partial_update / destroy: se resuelve por instancia en
        # has_object_permission (ahí sabemos a qué conferencia pertenece)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        user = getattr(request, 'auth_user', None) or self._get_authenticated_user(request)
        if user is None:
            raise NotAuthenticated("Usuario no autenticado.")

        if user.role == 'admin':
            return True

        if not obj.conference.chairs.filter(pk=user.id).exists():
            raise PermissionDenied(self.message)

        # En un update, evitar que el chair mueva la sesión a una conferencia
        # de la que no es chair.
        new_conference_id = request.data.get('conference_id') or request.data.get('conference')
        if new_conference_id and str(new_conference_id) != str(obj.conference_id):
            if not Conference.objects.filter(pk=new_conference_id, chairs=user).exists():
                raise PermissionDenied(self.message)

        return True
