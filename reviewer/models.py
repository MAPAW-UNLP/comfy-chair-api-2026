from django.db import models
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
from article.models import Article
from chair.models import ReviewAssignment
from conference.models import Conference
from notification.models import Notification
from user.models import User

class BaseReview(models.Model):
    score = models.IntegerField(
        validators=[MinValueValidator(-3), MaxValueValidator(3)]
    )
    opinion = models.TextField()

    class Meta:
        abstract = True  
    
class Review(BaseReview):
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    is_published = models.BooleanField(default=False) 
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now =True)

class ReviewVersion(BaseReview):
    review = models.ForeignKey(Review, on_delete=models.CASCADE,related_name='versions')
    version_number = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    
    
class Bid(models.Model):
    STATE_CHOICES = [
        ("Interesado", "Interesado"),
        ("No Interesado", "No Interesado"), 
        ("Quizás", "Quizás"),
        ("No_select", "No seleccionado"),  # <-- Agrega la etiqueta legible
    ]
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE)
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    choice = models.CharField(
        max_length=20,
        choices=STATE_CHOICES,
        default="No_select",
        null=True,
        blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reviewer", "article"], name="unique_bid_per_reviewer_article")
        ]


class ReviewerInvitationQuerySet(models.QuerySet):
    def with_effective_status(self):
        # "expired" no se guarda: es una invitación pendiente cuya fecha límite ya pasó
        expired = Q(status="pending", expires_at__isnull=False, expires_at__lt=timezone.now())
        return self.annotate(
            effective_status=Case(
                When(expired, then=Value("expired")),
                default=F("status"),
                output_field=models.CharField(),
            )
        )


# Invitación a un usuario para formar parte del comité de revisores de una conferencia
class ReviewerInvitation(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("accepted", "Aceptada"),
        ("rejected", "Rechazada"),
    ]
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reviewer_invitations")
    conference = models.ForeignKey(Conference, on_delete=models.CASCADE, related_name="reviewer_invitations")
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_reviewer_invitations"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = no vence
    responded_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True, default="")  # visible para los chairs

    objects = ReviewerInvitationQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["reviewer", "conference"],
                name="unique_reviewer_invitation_per_conference",
            )
        ]

    def __str__(self):
        return f"{self.reviewer.full_name} - {self.conference.title} ({self.status})"

    def is_expired(self):
        return self.status == "pending" and self.expires_at is not None and self.expires_at < timezone.now()


# Notificación de una invitación: hereda de Notification (sigue apareciendo en /notifications/
# sin cambiar ese modelo) y agrega la invitación, para poder aceptarla o rechazarla desde ahí.
class InvitationNotification(Notification):
    invitation = models.ForeignKey(ReviewerInvitation, on_delete=models.CASCADE, related_name="notifications")
