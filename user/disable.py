from django.utils import timezone
from chair.models import ReviewAssignment
from notification.models import Notification
from .chair_requests import (
    can_request_conference_chair,
    close_pending_requests,
    get_pending_request,
    pending_request_data,
)


class ChairConflictError(Exception):
    def __init__(self, conflicts):
        super().__init__("El usuario es el único chair de conferencias o sesiones en curso.")
        self.conflicts = conflicts


def active_conference_filter(prefix=''):
    # Las conferencias terminadas quedan como historial: no cuentan como
    # conflicto ni se modifican sus chairs o asignaciones.
    return {f'{prefix}end_date__gte': timezone.localdate()}


def _has_other_active_chair(obj, user):
    return obj.chairs.filter(is_active=True).exclude(pk=user.pk).exists()


def _conference_data(conference):
    return {'type': 'conference', 'id': conference.id, 'title': conference.title}


def _session_data(session):
    return {
        'type': 'session',
        'id': session.id,
        'title': session.title,
        'conference_id': session.conference_id,
        'conference_title': session.conference.title,
    }


def _session_conflict_data(session, user):
    return {
        **_session_data(session),
        'can_request_conference_chair': can_request_conference_chair(session, user),
        'pending_request': pending_request_data(get_pending_request(session, user)),
    }


def get_chair_impact(user):
    conferences = user.conferences.filter(**active_conference_filter()).order_by('title')
    sessions = (
        user.session.filter(**active_conference_filter('conference__'))
        .select_related('conference')
        .order_by('conference__title', 'title')
    )

    impact = {'conflicts': [], 'chair_removals': [], 'conferences': [], 'sessions': []}
    for conference in conferences:
        key = 'chair_removals' if _has_other_active_chair(conference, user) else 'conflicts'
        impact[key].append(_conference_data(conference))
        impact['conferences'].append(conference)
    for session in sessions:
        if _has_other_active_chair(session, user):
            impact['chair_removals'].append(_session_data(session))
        else:
            impact['conflicts'].append(_session_conflict_data(session, user))
        impact['sessions'].append(session)
    return impact


def _pending_assignments(user):
    # Pendiente = todavía no publicó la revisión (incluye los borradores).
    return (
        ReviewAssignment.objects.filter(
            reviewer=user,
            reviewed=False,
            deleted=False,
            **active_conference_filter('article__session__conference__'),
        )
        .select_related('article__session__conference')
        .order_by('article__session__title', 'article__title')
    )


def _group_by_session(assignments):
    sessions = {}
    for assignment in assignments:
        session = assignment.article.session
        sessions.setdefault(session.id, {'session': session, 'assignments': []})
        sessions[session.id]['assignments'].append(assignment)
    return list(sessions.values())


def get_pending_reviews(user):
    return [
        {
            'session_id': group['session'].id,
            'session_title': group['session'].title,
            'articles': [
                {'id': assignment.article.id, 'title': assignment.article.title}
                for assignment in group['assignments']
            ],
        }
        for group in _group_by_session(_pending_assignments(user))
    ]


def _notification_recipients(session, user):
    chairs = session.chairs.filter(is_active=True).exclude(pk=user.pk)
    if not chairs.exists():
        chairs = session.conference.chairs.filter(is_active=True).exclude(pk=user.pk)
    return chairs


def _release_pending_reviews(user):
    groups = _group_by_session(_pending_assignments(user))
    assignment_ids = [assignment.id for group in groups for assignment in group['assignments']]

    # update() no dispara el post_save de ReviewAssignment, así el revisor
    # deshabilitado no recibe el aviso de "Ya no estás asignado".
    ReviewAssignment.objects.filter(id__in=assignment_ids).update(deleted=True)

    notified_sessions = []
    for group in groups:
        session = group['session']
        articles = ', '.join(f'"{assignment.article.title}"' for assignment in group['assignments'])
        message = (
            f'El revisor {user.full_name} ({user.email}) ya no se encuentra habilitado. '
            f'Tenía asignados los siguientes artículos sin revisar en la sesión "{session.title}": '
            f'{articles}. Asigná nuevos revisores donde haga falta.'
        )
        recipients = list(_notification_recipients(session, user))
        Notification.objects.bulk_create([
            Notification(user=chair, title='Revisor deshabilitado', message=message, type='critical')
            for chair in recipients
        ])
        if recipients:
            notified_sessions.append({'session_id': session.id, 'session_title': session.title})

    return {'removed_assignments': len(assignment_ids), 'notified_sessions': notified_sessions}


def get_disable_check(user):
    impact = get_chair_impact(user)
    return {
        'user_id': user.id,
        'can_disable': not impact['conflicts'],
        'conflicts': impact['conflicts'],
        'chair_removals': impact['chair_removals'],
        'pending_reviews': get_pending_reviews(user),
    }


def disable_user(user):
    # Se llama dentro de una transacción: si hay conflictos no se modifica nada.
    impact = get_chair_impact(user)
    if impact['conflicts']:
        raise ChairConflictError(impact['conflicts'])

    close_pending_requests(user)
    for conference in impact['conferences']:
        conference.chairs.remove(user)
    for session in impact['sessions']:
        session.chairs.remove(user)

    summary = _release_pending_reviews(user)

    user.is_active = False
    user.save(update_fields=['is_active'])
    return summary
