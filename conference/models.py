from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from user.models import User
from django.core.exceptions import ValidationError

class Conference (models.Model):

    VISTA_CHOICES = [
        ('single blind', 'Single blind'),
        ('double blind', 'Double blind'),
        ('completo', 'Completo')
    ]
    
    title = models.CharField(max_length=50)

    description = models.CharField(max_length=300)
    start_date = models.DateField(
        null=False, 
        blank=False
    )
    end_date = models.DateField(
        null=False, 
        blank=False
    )
    # agrego las fechas de submission y review para la conferencia /grupo5
    submission_start = models.DateField(null=True, blank=True, default=None)
    submission_end = models.DateField(null=True, blank=True, default=None)
    review_start = models.DateField(null=True, blank=True, default=None)
    review_end = models.DateField(null=True, blank=True, default=None)
    # tipo de lectura 
    blind_kind = models.CharField(
        max_length=12,
        choices=VISTA_CHOICES,
        default='completo'
    )
    
    # lista de chairs
    chairs = models.ManyToManyField(
        User,
        blank=True,
        related_name='conferences'
    )
    
    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        for start_field, end_field in (
            ('submission_start', 'submission_end'),
            ('review_start', 'review_end'),
        ):
            start = getattr(self, start_field)
            end = getattr(self, end_field)
            if start and end and end < start:
                raise ValidationError({
                    end_field: f'{end_field} no puede ser anterior a {start_field}.'
                })

    class Meta:
        # la conferencia no acepta títulos duplicados no distinguimos mayúsculas/minúsculas
        constraints = [
            UniqueConstraint(
                Lower('title'),
                name='unique_conference_title_ci'
            )
        ]
