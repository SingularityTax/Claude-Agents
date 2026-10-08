from slamonitor.config import SlaSettings
from slamonitor.hours import BusinessClock

from .conftest import at

clock = BusinessClock(SlaSettings())


def test_due_same_day_morning():
    # Montag 08:30 + 10 h = Dienstag 08:30
    assert clock.add_business_hours(at(5, 8, 30), 10) == at(6, 8, 30)


def test_due_after_hours_starts_next_morning():
    # Montag 20:00 -> Fristbeginn Dienstag 08:00 -> Ende Dienstag 18:00
    assert clock.add_business_hours(at(5, 20), 10) == at(6, 18)


def test_weekend_skipped():
    # Freitag 16:00 + 10 h: 2 h Freitag, 8 h Montag -> Montag 16:00
    assert clock.add_business_hours(at(9, 16), 10) == at(12, 16)


def test_holiday_skipped():
    # 1.11.2026 (Allerheiligen, NRW) ist ein Sonntag, daher Test mit 3.10. auf Samstag nicht aussagekräftig.
    # 25.12.2026 ist Freitag (Feiertag): Donnerstag 24.12. 17:00 + 4 h -> 1 h Do, 3 h Montag 28.12. -> 11:00
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ber = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 12, 24, 17, tzinfo=ber)
    assert clock.add_business_hours(start, 4) == datetime(2026, 12, 28, 11, tzinfo=ber)


def test_business_hours_between():
    assert clock.business_hours_between(at(9, 16), at(12, 10)) == 4.0
    assert clock.business_hours_between(at(12, 10), at(9, 16)) == 0.0
