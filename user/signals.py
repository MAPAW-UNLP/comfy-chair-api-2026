from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from conference_session.models import Session
from .chair_requests import resolve_session_requests, resolve_user_requests


@receiver(m2m_changed, sender=Session.chairs.through)
def resolve_chair_replacement_requests(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return

    if not reverse:
        resolve_session_requests([instance.pk])
    elif pk_set:
        resolve_session_requests(pk_set)
    else:
        resolve_user_requests(instance)
