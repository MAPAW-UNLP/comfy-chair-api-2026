from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError
from django.db.models import Q
from .models import Conference
from .serializers import ConferenceSerializer
from django.utils import timezone
from .permissions import IsAdmin
from user.models import User

class ConferenceViewSet(viewsets.ModelViewSet):
    queryset = Conference.objects.all().order_by('-id')
    serializer_class = ConferenceSerializer
    permission_classes = [IsAdmin]
#para poder editar en chairs las ventanas de envio articulos y revision grupo5
    def get_permissions(self):
        if self.action == 'update_periods':
            return [AllowAny()]
        return super().get_permissions()

    @action(detail=True, methods=['patch'], url_path='periods')
    def update_periods(self, request, pk=None):
        conference = self.get_object()
        user_id = request.data.get('user_id')

        if not user_id:
            return Response(
                {'detail': 'Se debe especificar user_id.'},
                status=403
            )

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'detail': 'Usuario no encontrado.'}, status=403)

        is_admin = getattr(user, 'role', '').lower() == 'admin'
        is_chair = conference.chairs.filter(pk=user_id).exists()
        if not is_admin and not is_chair:
            return Response(
                {'detail': 'El usuario no es Chair de esta conferencia.'},
                status=403
            )

        allowed_fields = {
            'submission_start', 'submission_end',
            'review_start', 'review_end',
        }
        data = {
            field: request.data.get(field)
            for field in allowed_fields
            if field in request.data
        }
        serializer = self.get_serializer(conference, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def users(self, request, pk=None):
        conference = self.get_object()
        role = request.query_params.get('role', '').strip()
        valid_roles = {
            'conference_chair',
            'session_chair',
            'author',
            'reviewer',
        }
        if role and role not in valid_roles:
            raise ValidationError({
                'role': 'Rol inválido. Use conference_chair, session_chair, author o reviewer.'
            })

        search = request.query_params.get('search', '').strip()
        role_querysets = (
            ('conference_chair', conference.chairs.all()),
            (
                'session_chair',
                User.objects.filter(session__conference=conference).distinct()
            ),
            (
                'author',
                User.objects.filter(article__session__conference=conference).distinct()
            ),
            (
                'reviewer',
                User.objects.filter(
                    assignment_reviews__article__session__conference=conference,
                    assignment_reviews__deleted=False
                ).distinct()
            ),
        )

        users_by_id = {}
        for user_role, users in role_querysets:
            users = users.filter(deleted=False, is_active=True)
            if search:
                users = users.filter(
                    Q(full_name__icontains=search) |
                    Q(email__icontains=search)
                )

            for user in users.order_by('full_name', 'id'):
                user_data = users_by_id.setdefault(user.id, {
                    'id': user.id,
                    'full_name': user.full_name,
                    'email': user.email,
                    'affiliation': user.affiliation,
                    'roles': [],
                })
                user_data['roles'].append(user_role)

        users = list(users_by_id.values())
        if role:
            users = [
                user for user in users
                if role in user['roles']
            ]

        return Response({
            'conference_id': conference.id,
            'users': sorted(
                users,
                key=lambda user: (user['full_name'].casefold(), user['id'])
            ),
        })

    # /api/conference/finished/  -->  devuelve las conferencias con end_date < fecha_actual
    @action(detail=False, methods=['get'])
    def finished(self, request):
        fecha_actual = timezone.now().date()
        conferencias = Conference.objects.filter(
            end_date__lt=fecha_actual
        ).order_by('-id')  
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)
    
    # /api/conference/active/ --> devuelve las conferencias con end_date >= fecha_actual
    @action(detail=False, methods=['get'])
    def active(self, request):
        fecha_actual = timezone.now().date()
        conferencias = Conference.objects.filter(
            end_date__gte=fecha_actual
        ).order_by('-id') 
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)