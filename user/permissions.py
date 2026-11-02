from rest_framework.permissions import BasePermission
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from .models import User


class IsAdminUser(BasePermission):

    message = "Este usuario no posee los permisos necesarios."

    def has_permission(self, request, view):
        
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            raise NotAuthenticated("Usuario no autenticado.")

        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotAuthenticated("Usuario no autenticado.")

        if user.role != 'admin':
            raise PermissionDenied(self.message)

        return True
