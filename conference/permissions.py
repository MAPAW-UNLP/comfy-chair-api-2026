from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from user.access import authenticated_user

class IsAdmin(BasePermission):

    def has_permission(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True

        user = authenticated_user(request)
        
        if getattr(user, 'role', '').lower() != 'admin':
            raise PermissionDenied("Este usuario no posee los permisos necesarios.")
        
        return True
