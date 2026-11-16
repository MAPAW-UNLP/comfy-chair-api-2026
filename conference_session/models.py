from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone

from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from django.utils import timezone
from user.models import User
from conference.models import Conference
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

class Session(models.Model):
    title = models.CharField(max_length=200)
    deadline = models.DateField(
        null=False,
        blank=False
    )
    capacity = models.IntegerField(null=False, blank=False)
    conference = models.ForeignKey(
        Conference, 
        on_delete=models.CASCADE, 
        related_name='session'
    )
    # opcionales
    threshold_percentage = models.IntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(100)
        ],
        null=True,
        blank=True,
        default=None
    )
    improvement_threshold = models.FloatField(
        validators=[
            MinValueValidator(-3),
            MaxValueValidator(3)
        ],
        null=True,
        blank=True,
        default=None
    )

    chairs = models.ManyToManyField(
        User,
        blank=True,
        related_name='session'
    )
    locked_selection = models.BooleanField(
        default=False,
        blank=True,
        help_text="If true, the selection type cannot be modified."
    )

    TYPE_SELECTION_CHOICES = [
        ("ScoreThresholdSelection", "ScoreThresholdSelection"),
        ("CutoffSelection", "CutoffSelection"),
    ]

    type_selection = models.CharField(
        max_length=50,
        choices=TYPE_SELECTION_CHOICES,
        null=True,      # OPCIONAL
        blank=True,     # OPCIONAL
        help_text="Type of selection used for this session."
    )

    def __str__(self):
        return f"{self.title} ({self.conference.title})"

    def clean(self):
        # Validar que el deadline esté dentro del rango de la conferencia
        if self.conference and self.deadline:
            start = self.conference.start_date
            end = self.conference.end_date
            if start and end and (self.deadline < start or self.deadline > end):
                raise ValidationError({
                    'deadline': 'La fecha de deadline debe estar entre las fechas de inicio y fin de la conferencia.'
                })

    def save(self, *args, **kwargs):
        # ejecutar validaciones de modelo antes de guardar
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            # Título único por conferencia no distinguimos mayúsculas/minúsculas
            UniqueConstraint(
                Lower('title'), 
                'conference', 
                name='unique_session_title_per_conference_ci'
            )
        ]


# Conference submission window shared by all of its sessions. DateField
# boundaries are UTC instants; the end is exclusive.
UTC = datetime_timezone.utc


def _as_utc_instant(value, *, end_of_date=False):
    if isinstance(value, datetime):
        # A future DateTimeField keeps its time and timezone. Naive values are
        # interpreted in UTC, like the current DateField contract.
        if timezone.is_naive(value):
            value = timezone.make_aware(value, UTC)
        return value.astimezone(UTC)

    if not isinstance(value, date):
        raise TypeError("A submission boundary must be a date or datetime")
    if end_of_date:
        value += timedelta(days=1)
    return datetime.combine(value, time.min, tzinfo=UTC)


@dataclass(frozen=True)
class SubmissionPeriod:
    start: datetime | None
    end_exclusive: datetime | None

    def is_open(self, now=None):
        if (
            self.start is None
            or self.end_exclusive is None
            or self.start >= self.end_exclusive
        ):
            return False
        now = timezone.now() if now is None else now
        return self.start <= now.astimezone(UTC) < self.end_exclusive


def get_submission_period(session):
    """Resolve the conference boundaries for a valid session."""
    conference = session.conference
    return SubmissionPeriod(
        start=(
            _as_utc_instant(conference.submission_start)
            if conference.submission_start is not None else None
        ),
        end_exclusive=(
            _as_utc_instant(conference.submission_end, end_of_date=True)
            if conference.submission_end is not None else None
        ),
    )


def are_submission_periods_open(*sessions):
    """Use one server-clock instant for all sessions in an edit."""
    now = timezone.now()
    return all(get_submission_period(session).is_open(now) for session in sessions)
