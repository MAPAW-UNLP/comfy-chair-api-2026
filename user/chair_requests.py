from django.utils import timezone
from notification.models import Notification
from .models import ChairReplacementRequest


class ChairRequestError(Exception):
    pass


def _pending_requests():
    return ChairReplacementRequest.objects.filter(resolved_at__isnull=True)


def _conference_chairs_to_notify(session, user):
    return session.conference.chairs.filter(is_active=True).exclude(pk=user.pk).order_by('full_name')


def _is_only_active_chair(session, user):
    return (
        session.chairs.filter(pk=user.pk).exists()
        and not session.chairs.filter(is_active=True).exclude(pk=user.pk).exists()
    )


def can_request_conference_chair(session, user):
    return _conference_chairs_to_notify(session, user).exists()


def get_pending_request(session, user):
    return _pending_requests().filter(session=session, user=user).first()


def pending_request_data(chair_request):
    if chair_request is None:
        return None
    return {'id': chair_request.id, 'requested_at': chair_request.requested_at}


def request_conference_chair(user, session, admin):
    if session.conference.end_date < timezone.localdate():
        raise ChairRequestError('La conferencia de esa sesión ya terminó.')
    if not _is_only_active_chair(session, user):
        raise ChairRequestError('El usuario no es el único chair activo de esa sesión.')

    chairs = list(_conference_chairs_to_notify(session, user))
    if not chairs:
        raise ChairRequestError('La conferencia no tiene chairs activos a quienes avisar.')

    chair_request = get_pending_request(session, user)
    if chair_request is not None:
        return chair_request, chairs, False

    chair_request = ChairReplacementRequest.objects.create(user=user, session=session, requested_by=admin)
    message = (
        f'El administrador necesita deshabilitar a {user.full_name} ({user.email}), '
        f'que es el único chair de la sesión "{session.title}". Asigná otro chair a esa sesión.'
    )
    Notification.objects.bulk_create([
        Notification(user=chair, title='Sesión sin chair', message=message, type='critical')
        for chair in chairs
    ])
    return chair_request, chairs, True


def resolve_requests(requests):
    for chair_request in requests.select_related('user', 'session', 'requested_by'):
        if _is_only_active_chair(chair_request.session, chair_request.user):
            continue

        chair_request.resolved_at = timezone.now()
        chair_request.save(update_fields=['resolved_at'])
        if chair_request.requested_by is not None:
            Notification.objects.create(
                user=chair_request.requested_by,
                title='Conflicto resuelto',
                message=(
                    f'La sesión "{chair_request.session.title}" ya tiene otro chair. '
                    f'{chair_request.user.full_name} ya no tiene conflicto en esa sesión.'
                ),
                type='info',
            )


def resolve_session_requests(session_ids):
    resolve_requests(_pending_requests().filter(session_id__in=session_ids))


def resolve_user_requests(user):
    resolve_requests(_pending_requests().filter(user=user))


def close_pending_requests(user):
    _pending_requests().filter(user=user).update(resolved_at=timezone.now())
