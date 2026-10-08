"""Arbeitszeit-Rechnung: Mo–Fr, feste Tageszeiten, Feiertage (Standard NRW)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays

from .config import SlaSettings


class BusinessClock:
    def __init__(self, settings: SlaSettings):
        self.tz = ZoneInfo(settings.timezone)
        self.start = time.fromisoformat(settings.workday_start)
        self.end = time.fromisoformat(settings.workday_end)
        self._holidays = holidays.country_holidays("DE", subdiv=settings.holidays_subdivision)

    def is_workday(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def _window(self, d: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(d, self.start, tzinfo=self.tz),
            datetime.combine(d, self.end, tzinfo=self.tz),
        )

    def business_hours_between(self, a: datetime, b: datetime) -> float:
        """Arbeitsstunden zwischen a und b (b < a ergibt 0)."""
        if b <= a:
            return 0.0
        a, b = a.astimezone(self.tz), b.astimezone(self.tz)
        total = timedelta()
        d = a.date()
        while d <= b.date():
            if self.is_workday(d):
                ws, we = self._window(d)
                lo, hi = max(a, ws), min(b, we)
                if hi > lo:
                    total += hi - lo
            d += timedelta(days=1)
        return total.total_seconds() / 3600

    def add_business_hours(self, start: datetime, hours: float) -> datetime:
        """Zeitpunkt, an dem ab `start` die angegebenen Arbeitsstunden verstrichen sind."""
        remaining = timedelta(hours=hours)
        cur = start.astimezone(self.tz)
        while True:
            d = cur.date()
            if self.is_workday(d):
                ws, we = self._window(d)
                if cur < ws:
                    cur = ws
                if cur < we:
                    available = we - cur
                    if remaining <= available:
                        return cur + remaining
                    remaining -= available
            cur = datetime.combine(d + timedelta(days=1), time(0), tzinfo=self.tz)
