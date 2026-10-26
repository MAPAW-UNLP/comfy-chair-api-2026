from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ROLES = (
        ("user", "User"), #valor_en_bd, etiqueta_visible
        ("admin", "Admin"),
    )
    full_name = models.CharField(max_length=150)
    affiliation = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLES)
    deleted = models.BooleanField(default=False)

    username = None  # Eliminamos el username

    USERNAME_FIELD = "email"  #identificador único
    REQUIRED_FIELDS = ["full_name", "affiliation"]

    def __str__(self):
        return f"{self.affiliation} ({self.email})"

    class Meta:
        verbose_name_plural = "user"


# Grupo 3 (2026)
class ChairReplacementRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chair_replacement_requests')
    session = models.ForeignKey(
        'conference_session.Session', on_delete=models.CASCADE, related_name='chair_replacement_requests'
    )
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
