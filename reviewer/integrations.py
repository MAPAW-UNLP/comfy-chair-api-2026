"""Puntos de contacto con datos que dependen de otros grupos (Chairs)."""
from datetime import datetime, time, timezone as dt_timezone
from typing import NamedTuple

from django.utils import timezone

BIDDING_FIELDS = ("bidding_start", "bidding_end")
REVIEW_FIELDS = ("review_start", "review_end")


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


class Window(NamedTuple):
    start: datetime | None
    end: datetime | None

    def is_open(self, now=None):
        now = now or timezone.now()
        return (self.start is None or self.start <= now) and (self.end is None or now <= self.end)

    def as_dict(self, now=None):
        return {"start": _iso(self.start), "end": _iso(self.end), "is_open": self.is_open(now)}


def _aware(value, end_of_day=False):
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.max if end_of_day else time.min)
    if timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)


def _window(session, fields):
    conference = getattr(session, "conference", None)
    start, end = (getattr(session, f, None) or getattr(conference, f, None) for f in fields)
    return Window(_aware(start), _aware(end, end_of_day=True))


def bidding_window(session):
    return _window(session, BIDDING_FIELDS)


def review_window(session):
    return _window(session, REVIEW_FIELDS)
