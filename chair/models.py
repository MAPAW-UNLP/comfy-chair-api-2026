from django.db import models
from django.contrib.auth import get_user_model
from conference_session.models import Session
from article.models import Article
from user.models import User
from django.utils import timezone
from datetime import timedelta

class ReviewAssignment(models.Model):
    reviewer = models.ForeignKey(
        'user.User',
        on_delete=models.CASCADE,
        related_name='assignment_reviews'
    )
    article = models.ForeignKey('article.Article', on_delete=models.CASCADE)
    reviewed = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('reviewer', 'article')


class ReviewerInvitation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('accepted', 'Aceptada'),
        ('rejected', 'Rechazada'),
        ('expired', 'Vencida'),
    ]

    # A qué sesión se le está invitando
    session = models.ForeignKey('conference_session.Session', on_delete=models.CASCADE, related_name='invitations')
    
    # A quién va dirigida la invitación
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_invitations')
    
    # Estado actual de la invitación (coincide con las pestañas del frontend)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Control de fechas
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        # Evita que se envíen múltiples invitaciones a la misma persona para la misma sesión
        unique_together = ('session', 'user')

    def save(self, *args, **kwargs):
        # Asignar automáticamente una fecha de vencimiento (ej: 7 días) si no se provee una
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)
