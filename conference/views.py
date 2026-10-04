from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from .models import Conference
from .serializers import ConferenceSerializer
from django.utils import timezone
from .permissions import IsAdmin
from user.models import User

class ConferenceViewSet(viewsets.ModelViewSet):
    queryset = Conference.objects.all().order_by('-id')
    serializer_class = ConferenceSerializer
    permission_classes = [IsAdmin]

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
                users = users.filter(full_name__icontains=search)

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
