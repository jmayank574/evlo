"""Relative load-date resolution: synthetic loads must read as future-dated
regardless of when the link is opened (resolved at request time, not seed time)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Load
from app.services.loads import resolve_windows


def _offset_load() -> Load:
    return Load(
        reference="X", origin_label="A", origin_lat=Decimal("0"), origin_lon=Decimal("0"),
        dest_label="B", dest_lat=Decimal("0"), dest_lon=Decimal("0"), weight_lb=1000,
        data_source="synthetic",
        pickup_start_offset_h=Decimal("30"), pickup_end_offset_h=Decimal("33"),
        delivery_start_offset_h=Decimal("40"), delivery_end_offset_h=Decimal("44"),
    )


def test_offsets_resolve_relative_to_today_and_stay_future():
    load = _offset_load()
    # Simulate opening the link today, in 3 days, in a month, in over a year.
    for days_ahead in (0, 3, 30, 400):
        now = datetime(2026, 6, 15, 14, 30, tzinfo=timezone.utc) + timedelta(days=days_ahead)
        w = resolve_windows(load, now)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        assert w.delivery_end == base + timedelta(hours=44)
        assert w.pickup_start == base + timedelta(hours=30)
        # A 44h offset is ~1.8 days out, so the deadline is always in the future.
        assert w.delivery_end > now


def test_absolute_windows_pass_through_unchanged():
    """Real (uploaded) loads carry absolute windows and are not shifted."""
    abs_dt = datetime(2030, 1, 1, 6, tzinfo=timezone.utc)
    load = Load(
        reference="REAL", origin_label="A", origin_lat=Decimal("0"), origin_lon=Decimal("0"),
        dest_label="B", dest_lat=Decimal("0"), dest_lon=Decimal("0"), weight_lb=1000,
        data_source="real",
        pickup_window_start=abs_dt, pickup_window_end=abs_dt,
        delivery_window_start=abs_dt, delivery_window_end=abs_dt,
    )
    w = resolve_windows(load, datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert w.pickup_start == abs_dt and w.delivery_end == abs_dt
