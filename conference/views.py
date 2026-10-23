from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError
from rest_framework import serializers
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from article.models import Article, ArticleHistory
from chair.models import ReviewAssignment
from conference_session.models import Session
from notification.models import Notification
from user.disable import _group_by_session, _notification_recipients
from .models import Conference
from .serializers import ConferenceSerializer
from django.utils import timezone
from .permissions import IsAdmin
from user.models import User


class ConferenceUserRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=('conference_chair', 'session_chair', 'author', 'reviewer')
    )
    action = serializers.ChoiceField(choices=('add', 'remove'))
    session_id = serializers.IntegerField(required=False)
    article_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        role = attrs['role']
        operation = attrs['action']
        supplied_scope = set(attrs) & {'session_id', 'article_id'}
        expected_scope = None
        if role == 'session_chair':
            expected_scope = 'session_id'
        elif role == 'reviewer' and operation == 'add':
            expected_scope = 'article_id'

        if expected_scope and supplied_scope != {expected_scope}:
            raise serializers.ValidationError({
                expected_scope: 'Este campo es obligatorio para la operación solicitada.'
            })
        if not expected_scope and supplied_scope:
            raise serializers.ValidationError(
                'El rol y la operación seleccionados no admiten session_id ni article_id.'
            )
        return attrs


class ConferenceViewSet(viewsets.ModelViewSet):
    queryset = Conference.objects.all().order_by('-id')
    serializer_class = ConferenceSerializer
    permission_classes = [IsAdmin]
#para poder editar en chairs las ventanas de envio articulos y revision grupo5
    def get_permissions(self):
        if self.action in ('update_periods', 'update_user_role'):
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

    @action(
        detail=True,
        methods=['patch'],
        url_path=r'users/(?P<user_id>\d+)/role',
    )
    def update_user_role(self, request, pk=None, user_id=None):
        serializer = ConferenceUserRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data['role']
        operation = data['action']

        if role == 'author':
            raise ValidationError({
                'code': 'author_role_immutable',
                'detail': 'El rol de autor no se puede quitar ni modificar.',
            })

        with transaction.atomic():
            conference = get_object_or_404(
                Conference.objects.select_for_update(),
                pk=pk,
            )
            actor = User.objects.select_for_update().filter(
                pk=getattr(request, 'user_id', None),
                is_active=True,
                deleted=False,
            ).first()
            if actor is None:
                return Response({
                    'code': 'account_not_available',
                    'detail': 'La cuenta autenticada no existe o no está habilitada.',
                }, status=401)

            target = User.objects.select_for_update().filter(
                pk=user_id,
                is_active=True,
                deleted=False,
            ).first()
            if target is None:
                return Response({
                    'code': 'user_not_available',
                    'detail': 'El usuario no existe o no está habilitado.',
                }, status=404)

            is_admin = actor.role == 'admin'
            is_conference_chair = conference.chairs.filter(
                pk=actor.pk,
                is_active=True,
                deleted=False,
            ).exists()
            if role == 'conference_chair':
                authorized = is_admin
            else:
                authorized = is_admin or is_conference_chair
            if not authorized:
                return Response({
                    'detail': 'No tenés permisos para modificar este rol en la conferencia.'
                }, status=403)

            if role == 'conference_chair':
                return self._update_conference_chair(conference, target, operation)

            if role == 'session_chair':
                session = get_object_or_404(
                    Session.objects.select_for_update(),
                    pk=data['session_id'],
                    conference_id=conference.pk,
                )
                return self._update_session_chair(
                    conference,
                    session,
                    target,
                    operation,
                )

            return self._update_reviewer(
                conference,
                target,
                actor,
                operation,
                data.get('article_id'),
            )

    @staticmethod
    def _update_conference_chair(conference, target, operation):
        relation = conference.chairs
        exists = relation.filter(pk=target.pk).exists()
        if operation == 'add':
            if exists:
                return ConferenceViewSet._role_unchanged('conference_chair', operation)
            relation.add(target)
        else:
            if not exists:
                return ConferenceViewSet._role_not_assigned('conference_chair')
            has_another_active_chair = relation.filter(
                is_active=True,
                deleted=False,
            ).exclude(pk=target.pk).exists()
            if not has_another_active_chair:
                ConferenceViewSet._notify_chair_conflict(
                    conference=conference,
                    target=target,
                    session=None,
                )
                return Response({
                    'code': 'last_conference_chair',
                    'detail': (
                        'No se puede quitar al último chair activo de la conferencia. '
                        'Primero debe asignarse otro chair.'
                    ),
                }, status=409)
            relation.remove(target)
        return ConferenceViewSet._role_changed(
            conference, target, 'conference_chair', operation
        )

    @staticmethod
    def _update_session_chair(conference, session, target, operation):
        relation = session.chairs
        exists = relation.filter(pk=target.pk).exists()
        if operation == 'add':
            if exists:
                return ConferenceViewSet._role_unchanged('session_chair', operation)
            relation.add(target)
        else:
            if not exists:
                return ConferenceViewSet._role_not_assigned('session_chair')
            has_another_active_chair = relation.filter(
                is_active=True,
                deleted=False,
            ).exclude(pk=target.pk).exists()
            if not has_another_active_chair:
                ConferenceViewSet._notify_chair_conflict(
                    conference=conference,
                    target=target,
                    session=session,
                )
                return Response({
                    'code': 'last_session_chair',
                    'detail': (
                        'No se puede quitar al último chair activo de la sesión. '
                        'Primero debe asignarse otro chair para evitar que quede huérfana.'
                    ),
                }, status=409)
            relation.remove(target)
        return ConferenceViewSet._role_changed(
            conference, target, 'session_chair', operation
        )

    @staticmethod
    def _update_reviewer(conference, target, actor, operation, article_id):
        if operation == 'add':
            article = get_object_or_404(
                Article.objects.select_for_update(),
                pk=article_id,
                session__conference_id=conference.pk,
            )
            if article.authors.filter(pk=target.pk).exists():
                return Response({
                    'code': 'author_cannot_review',
                    'detail': 'Un autor no puede ser asignado como revisor de su propio artículo.',
                }, status=409)
            assignment, created = ReviewAssignment.objects.select_for_update().get_or_create(
                reviewer=target,
                article=article,
                defaults={'deleted': False},
            )
            if not created and not assignment.deleted:
                return ConferenceViewSet._role_unchanged('reviewer', operation)
            if not created:
                assignment.deleted = False
                assignment.save(update_fields=['deleted'])
                Notification.objects.create(
                    user=target,
                    article=article,
                    title='Revisión asignada',
                    message=f'Se te ha asignado revisar el artículo "{article.title}".',
                    type='info',
                )
            ArticleHistory.objects.create(
                article=article,
                reviewed_by=target,
                created_by=actor,
                event_type='reviewer_assigned',
            )
            return ConferenceViewSet._role_changed(conference, target, 'reviewer', operation)

        assignments = list(
            ReviewAssignment.objects.select_for_update().filter(
                reviewer=target,
                deleted=False,
                article__session__conference=conference,
            ).select_related('article__session').order_by('article__session_id', 'article_id')
        )
        if not assignments:
            return ConferenceViewSet._role_not_assigned('reviewer')

        ReviewAssignment.objects.filter(
            pk__in=[assignment.pk for assignment in assignments]
        ).update(deleted=True)

        pending_groups = _group_by_session(
            assignment for assignment in assignments if not assignment.reviewed
        )
        if pending_groups:
            notifications = []
            for group in pending_groups:
                session = group['session']
                titles = ', '.join(
                    f'"{assignment.article.title}"'
                    for assignment in group['assignments']
                )
                message = (
                    f'Se quitó el rol de revisor a {target.full_name} (ID: {target.pk}) '
                    f'en la conferencia "{conference.title}" (ID: {conference.pk}). '
                    f'En la sesión "{session.title}" (ID: {session.pk}) quedaron sin '
                    f'revisor las asignaciones pendientes de: {titles}. Asigná nuevos '
                    'revisores para esos artículos.'
                )
                recipients = _notification_recipients(session, target)
                notifications.extend(
                    Notification(
                        user_id=chair.pk,
                        title='Reasignar revisiones pendientes',
                        message=message,
                        type='critical',
                    )
                    for chair in recipients
                )
            Notification.objects.bulk_create(notifications)

        return Response({
            'conference_id': conference.pk,
            'user_id': target.pk,
            'role': 'reviewer',
            'action': operation,
            'changed': True,
            'removed_assignments': len(assignments),
            'pending_assignments_to_reassign': sum(
                len(group['assignments']) for group in pending_groups
            ),
        })

    @staticmethod
    def _role_changed(conference, target, role, operation):
        return Response({
            'conference_id': conference.pk,
            'user_id': target.pk,
            'role': role,
            'action': operation,
            'changed': True,
        })

    @staticmethod
    def _role_unchanged(role, operation):
        return Response({
            'role': role,
            'action': operation,
            'changed': False,
            'detail': 'La operación no requiere cambios.',
        })

    @staticmethod
    def _role_not_assigned(role):
        return Response({
            'code': 'role_not_assigned',
            'detail': f'El usuario no tiene una asignación activa del rol {role} en la conferencia.',
        }, status=409)

    @staticmethod
    def _notify_chair_conflict(conference, target, session):
        if session is None:
            recipients = User.objects.filter(
                role='admin',
                is_active=True,
                deleted=False,
            )
            title = 'No se puede quitar el último chair de la conferencia'
            message = (
                f'No se pudo quitar a {target.full_name} (ID: {target.pk}) como chair de la '
                f'conferencia "{conference.title}" (ID: {conference.pk}) porque es su último '
                'chair activo. Primero debe asignarse otro chair. '
                f'Administrar conferencia: /api/conference/{conference.pk}/'
            )
        else:
            recipients = conference.chairs.filter(
                is_active=True,
                deleted=False,
            )
            title = 'No se puede quitar el último chair de la sesión'
            message = (
                f'No se pudo quitar a {target.full_name} (ID: {target.pk}) como chair de la '
                f'sesión "{session.title}" (ID: {session.pk}) de la conferencia '
                f'"{conference.title}" (ID: {conference.pk}) porque es su último chair activo. '
                'Primero debe asignarse otro chair para evitar que quede huérfana. '
                f'Administrar sesión: /api/session/{session.pk}/'
            )

        Notification.objects.bulk_create([
            Notification(
                user_id=recipient_id,
                title=title,
                message=message,
                type='critical',
            )
            for recipient_id in set(recipients.values_list('pk', flat=True))
        ])

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search')
        if self.action in ('list', 'finished', 'active') and search and search.strip():
            search = search.strip()
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    # /api/conference/finished/  -->  devuelve las conferencias con end_date < fecha_actual
    @action(detail=False, methods=['get'])
    def finished(self, request):
        fecha_actual = timezone.now().date()
        conferencias = self.get_queryset().filter(
            end_date__lt=fecha_actual
        ).order_by('-id')  
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)
    
    # /api/conference/active/ --> devuelve las conferencias con end_date >= fecha_actual
    @action(detail=False, methods=['get'])
    def active(self, request):
        fecha_actual = timezone.now().date()
        conferencias = self.get_queryset().filter(
            end_date__gte=fecha_actual
        ).order_by('-id') 
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)