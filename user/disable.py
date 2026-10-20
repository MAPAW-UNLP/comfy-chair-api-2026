from django.utils import timezone
from chair.models import ReviewAssignment


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
        key = 'chair_removals' if _has_other_active_chair(session, user) else 'conflicts'
        impact[key].append(_session_data(session))
        impact['sessions'].append(session)
    return impact


def get_pending_reviews(user):
    assignments = (
        ReviewAssignment.objects.filter(
            reviewer=user,
            reviewed=False,
            deleted=False,
            **active_conference_filter('article__session__conference__'),
        )
        .select_related('article__session')
        .order_by('article__session__title', 'article__title')
    )

    sessions = {}
    for assignment in assignments:
        session = assignment.article.session
        entry = sessions.setdefault(session.id, {
            'session_id': session.id,
            'session_title': session.title,
            'articles': [],
        })
        entry['articles'].append({'id': assignment.article.id, 'title': assignment.article.title})
    return list(sessions.values())


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

    for conference in impact['conferences']:
        conference.chairs.remove(user)
    for session in impact['sessions']:
        session.chairs.remove(user)

    user.is_active = False
    user.save(update_fields=['is_active'])
