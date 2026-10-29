import jwt
from datetime import datetime, timedelta
from django.conf import settings
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework import generics, status
from .models import User
from .serializers import UserSerializer, LoginSerializer
from article.models import Article
from reviewer.models import Review, Bid
from django.db.models import OuterRef, Subquery
from django.db import transaction
from chair.models import ReviewAssignment
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.hashers import check_password
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import User
from .serializers import UserSerializer
from rest_framework.permissions import AllowAny
from .permissions import IsAdminUser
from .middleware import ACCOUNT_DISABLED_ERROR
from .disable import ChairConflictError, disable_user, get_disable_check
from .chair_requests import ChairRequestError, pending_request_data, request_conference_chair
from conference_session.models import Session

class UserRegisterAPI(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def perform_create(self, serializer):
        serializer.create(self.request.data, role="user")  

class AdminRegisterAPI(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def perform_create(self, serializer):
        serializer.create(self.request.data, role="admin")  

class LoginAPI(APIView):
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            user = serializer.validated_data["user"]

            payload = {
                "user_id": user.id,
                "rol": user.role,
                "exp": datetime.utcnow() + timedelta(seconds=settings.JWT_EXP_DELTA_SECONDS),
                "iat": datetime.utcnow()
            }
            token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

            return JsonResponse({
                "status": "ok",
                "token": token,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "affiliation": user.affiliation,
                    "role": user.role,
                } 
            }, status=200)

        # authenticate() también rechaza a los usuarios deshabilitados. Solo se avisa
        # que la cuenta está deshabilitada si la contraseña es correcta, para no
        # revelar qué cuentas existen.
        email = request.data.get("email")
        password = request.data.get("password")
        disabled_user = User.objects.filter(email=email, is_active=False).first() if email and password else None
        if disabled_user and disabled_user.check_password(password):
            return JsonResponse(ACCOUNT_DISABLED_ERROR, status=403)

        return JsonResponse(
            {"error": "Email o contraseña incorrecto."},
            status=401
        )

class GetUserIdAPI(APIView):
    def get(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return JsonResponse({'error': 'Usuario no autenticado'}, status=401)
        
        try:
            usuario = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)
        
        return JsonResponse({
            'id': usuario.id,
            'full_name': usuario.full_name,
            'email': usuario.email,
            'affiliation': usuario.affiliation,
            'role': usuario.role,
        }, status=200)
    
class GetUserListAPI(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        usuarios = User.objects.all().order_by('full_name', 'id')
        usuarios_data = [
            {
                'id': usuario.id,
                'full_name': usuario.full_name,
                'email': usuario.email,
                'affiliation': usuario.affiliation,
                'role': usuario.role,
                'is_active': usuario.is_active,
            } for usuario in usuarios
        ]
        return JsonResponse(usuarios_data, safe=False, status=200)

class DisableCheckAPI(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        return JsonResponse(get_disable_check(user), status=200)

class UpdateUserStatusAPI(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, user_id):
        is_active = request.data.get('is_active')
        if not isinstance(is_active, bool):
            return JsonResponse({'error': 'El campo is_active es obligatorio y debe ser true o false.'}, status=400)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        if not is_active and user.id == request.user_id:
            return JsonResponse({'error': 'No podés deshabilitar tu propia cuenta.'}, status=400)

        summary = {}
        with transaction.atomic():
            if is_active:
                # Al rehabilitar solo cambia el estado: no se restauran roles.
                user.is_active = True
                user.save(update_fields=['is_active'])
            else:
                try:
                    summary = disable_user(user)
                except ChairConflictError as error:
                    return JsonResponse({
                        'error': 'No se puede deshabilitar al usuario: es el único chair de las siguientes conferencias o sesiones.',
                        'conflicts': error.conflicts,
                    }, status=409)

        return JsonResponse({
            'message': 'Usuario habilitado correctamente.' if is_active else 'Usuario deshabilitado correctamente.',
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email,
                'affiliation': user.affiliation,
                'role': user.role,
                'is_active': user.is_active,
            },
            **summary,
        }, status=200)

class ChairReplacementRequestAPI(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, user_id):
        session_id = request.data.get('session_id')
        if not isinstance(session_id, int) or isinstance(session_id, bool):
            return JsonResponse({'error': 'El campo session_id es obligatorio y debe ser un número.'}, status=400)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        session = Session.objects.select_related('conference').filter(pk=session_id).first()
        if session is None:
            return JsonResponse({'error': 'Sesión no encontrada'}, status=404)

        admin = User.objects.get(pk=request.user_id)
        try:
            with transaction.atomic():
                chair_request, chairs, created = request_conference_chair(user, session, admin)
        except ChairRequestError as error:
            return JsonResponse({'error': str(error)}, status=400)

        return JsonResponse({
            **pending_request_data(chair_request),
            'session_id': session.id,
            'session_title': session.title,
            'conference_id': session.conference_id,
            'conference_title': session.conference.title,
            'notified_chairs': [chair.full_name for chair in chairs],
        }, status=201 if created else 200)

class GetUserFullDataAPI(APIView):
    def get(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return JsonResponse({'error': 'Usuario no autenticado'}, status=401)

        try:
            usuario = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        # Subquery para obtener score y opinion si ya existe revisión
        reviews_subquery = Review.objects.filter(
            reviewer=usuario,
            article=OuterRef('article')
        ).values('score', 'opinion')[:1]

        # Artículos donde el usuario es autor
        authored_articles = Article.objects.filter(authors=usuario).select_related('session__conference')

        # Artículos asignados al usuario como revisor
        assigned_reviews = ReviewAssignment.objects.filter(reviewer=usuario).annotate(
            score=Subquery(reviews_subquery.values('score')),
            opinion=Subquery(reviews_subquery.values('opinion'))
        ).select_related('article__session__conference')

        # Conferencias donde es chair general
        conference_chairs = usuario.conferences.all().values('id', 'title')

        # Sesiones donde es chair
        session_chairs = usuario.session.all().select_related('conference').values(
            'id', 'title', 'conference__id', 'conference__title')

        # Estructura jerárquica: conferencia → sesión → roles → artículos
        conferences_data = {}

        # Procesar artículos donde es autor
        for art in authored_articles:
            conf = art.session.conference
            sess = art.session
            conf_entry = conferences_data.setdefault(conf.id, {
                'conference_id': conf.id,
                'conference_name': conf.title,
                'sessions': {}
            })
            sess_entry = conf_entry['sessions'].setdefault(sess.id, {
                'session_id': sess.id,
                'session_name': sess.title,
                'roles': {}
            })
            role_entry = sess_entry['roles'].setdefault('autor', [])
            role_entry.append({
                'id': art.id,
                'title': art.title,
                'status': art.status,
                'type': art.type,
            })

        # Procesar artículos donde es revisor
        for assign in assigned_reviews:
            art = assign.article
            conf = art.session.conference
            sess = art.session
            conf_entry = conferences_data.setdefault(conf.id, {
                'conference_id': conf.id,
                'conference_name': conf.title,
                'sessions': {}
            })
            sess_entry = conf_entry['sessions'].setdefault(sess.id, {
                'session_id': sess.id,
                'session_name': sess.title,
                'roles': {}
            })
            role_entry = sess_entry['roles'].setdefault('revisor', [])
            role_entry.append({
                'id': art.id,
                'title': art.title,
                'score': assign.score,
                'opinion': assign.opinion,
            })

        # Convertir dicts a listas
        conferences_list = []
        for conf_data in conferences_data.values():
            sessions_list = []
            for sess_data in conf_data['sessions'].values():
                roles_list = []
                for role, arts in sess_data['roles'].items():
                    roles_list.append({'role': role, 'articles': arts})
                sess_data['roles'] = roles_list
                sessions_list.append(sess_data)
            conf_data['sessions'] = sessions_list
            conferences_list.append(conf_data)

        # Estructura final del usuario
        data = {
            'user': {
                'id': usuario.id,
                'full_name': usuario.full_name,
                'email': usuario.email,
                'affiliation': usuario.affiliation,
                'role': usuario.role,
                'is_conference_chair': conference_chairs.exists(),
                'conference_chair_of': list(conference_chairs),
                'is_session_chair': session_chairs.exists(),
                'session_chair_of': list(session_chairs),
            },
            'conferences': conferences_list
        }

        return JsonResponse(data, safe=False, status=200)

# Lo agregamos desde el grupo 3 para obtener usuarios que sean solo user, se ordena alfabeticamente
# Grupo 3 (2026): es el endpoint de los selectores de usuarios; no incluye a los deshabilitados
class GetUsersNoAdminAPI(APIView):
    def get(self, request):
        users = User.objects.filter(role="user", is_active=True).order_by('full_name')
        users_data = [
            {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email,
            } for user in users
        ]
        return JsonResponse(users_data, safe=False, status=200)


class UpdateUserDataAPI(APIView):
    def put(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return JsonResponse({'error': 'Usuario no autenticado'}, status=401)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        data = request.data
        full_name = data.get('full_name')
        affiliation = data.get('affiliation')
        email = data.get('email')

        # Validar email duplicado si es distinto
        if email and email != user.email:
            if User.objects.filter(email=email).exists():
                return JsonResponse({'error': 'El email ya está registrado por otro usuario.'}, status=400)
            user.email = email

        # Actualizar campos básicos
        if full_name:
            user.full_name = full_name
        if affiliation:
            user.affiliation = affiliation

        user.save()

        return JsonResponse({
            'message': 'Datos actualizados correctamente.',
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'affiliation': user.affiliation,
                'email': user.email,
            }
        }, status=200)


class UpdateUserPasswordAPI(APIView):
    def put(self, request):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return JsonResponse({'error': 'Usuario no autenticado'}, status=401)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        data = request.data
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')

        # Validaciones 
        if not all([current_password, new_password, confirm_password]):
            return JsonResponse({'error': 'Todos los campos son obligatorios.'}, status=400)

        if not user.check_password(current_password):
            return JsonResponse({'error': 'La contraseña actual es incorrecta.'}, status=400)

        if new_password != confirm_password:
            return JsonResponse({'error': 'Las contraseñas nuevas no coinciden.'}, status=400)

        if current_password == new_password:
            return JsonResponse({'error': 'La nueva contraseña no puede ser igual a la actual.'}, status=400)

        # Actualizar contraseña
        user.set_password(new_password)
        user.save()

        return JsonResponse({'message': 'Contraseña actualizada correctamente.'}, status=200)

class GetSessionReviewersAPI(APIView):

    def get(self, request, session_id, *args, **kwargs):
        reviewers = User.objects.filter(role="user", deleted=False, is_active=True)
        ## reviewers = reviewers.exclude(session__id=session_id) # excluye al chair de la sesion
        serializer = UserSerializer(reviewers, many=True)
        return Response(serializer.data, status=200)