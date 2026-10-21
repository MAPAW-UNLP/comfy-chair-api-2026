from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from reviewer.models import InvitationNotification, ReviewerInvitation


# Notifica al usuario cuando recibe una invitación para ser revisor de una conferencia.
# Es una InvitationNotification (hereda de Notification): se ve en /notifications/ como las demás
# y, como conoce su invitación, permite aceptarla o rechazarla desde la notificación.
@receiver(post_save, sender=ReviewerInvitation)
def reviewer_invitation_created_notification(sender, instance, created, **kwargs):
    if not created:
        return

    invited_by = instance.invited_by.full_name if instance.invited_by else 'Un chair'
    deadline = (
        f" Tenés tiempo de responder hasta el {timezone.localtime(instance.expires_at).strftime('%d/%m/%Y %H:%M')}."
        if instance.expires_at else ''
    )
    InvitationNotification.objects.create(
        invitation=instance,
        user=instance.reviewer,
        title="Nueva invitación para revisar",
        message=(
            f"{invited_by} te invitó a formar parte del comité de revisores de la conferencia "
            f"'{instance.conference.title}'.{deadline} Podés aceptarla o rechazarla desde esta notificación o desde Revisor → Invitaciones."
        ),
        type="info",
    )
