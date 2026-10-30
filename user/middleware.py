import jwt
from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from jwt import ExpiredSignatureError, InvalidTokenError
from .models import User

ACCOUNT_DISABLED_ERROR = {
    'error': 'Su cuenta se encuentra deshabilitada',
    'code': 'account_disabled',
}

class JWTAuthenticationMiddleware(MiddlewareMixin):
    def process_request(self, request):

        # Rutas que no requieren token (with and without trailing slash)
        public_paths = [
            '/user/login/',
            '/user/login',
            '/user/register/',
            '/user/register',
            '/user/register-admin/',
            '/user/register-admin',
        ]
        if request.path in public_paths:
            return None
     
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return JsonResponse({'error': 'Authorization header requerido'}, status=401)

        # Formato: "Bearer <token>"
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return JsonResponse({'error': 'Formato de token inválido'}, status=401)

        token = parts[1]
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )
        except ExpiredSignatureError:
            return JsonResponse({'error': 'Token expirado'}, status=401)
        except InvalidTokenError:
            return JsonResponse({'error': 'Token inválido'}, status=401)

        user_id = payload.get('user_id')

        # El token sigue siendo válido aunque el admin deshabilite la cuenta,
        # por eso se consulta el estado del usuario en cada request.
        user = User.objects.filter(pk=user_id).only('is_active').first()
        if user is None:
            return JsonResponse({'error': 'Token inválido'}, status=401)
        if not user.is_active:
            return JsonResponse(ACCOUNT_DISABLED_ERROR, status=401)

        # Se guarda user_id en el request
        request.user_id = user_id