"""Resolve a load's pickup/delivery windows to absolute datetimes.

Synthetic sample loads store RELATIVE offsets (hours from today 00:00 UTC) so
their dates never go stale. Resolution happens at REQUEST time against the
current date — never at seed time (which would just relocate the staleness).
Real (uploaded) loads carry absolute windows and pass through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import Load


@dataclass(frozen=True)
class Windows:
    pickup_start: datetime
    pickup_end: datetime
    delivery_start: datetime
    delivery_end: datetime


def resolve_windows(load: Load, now: datetime | None = None) -> Windows:
    now = now or datetime.now(timezone.utc)
    base = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def r(offset, absolute):
        return base + timedelta(hours=float(offset)) if offset is not None else absolute

    return Windows(
        pickup_start=r(load.pickup_start_offset_h, load.pickup_window_start),
        pickup_end=r(load.pickup_end_offset_h, load.pickup_window_end),
        delivery_start=r(load.delivery_start_offset_h, load.delivery_window_start),
        delivery_end=r(load.delivery_end_offset_h, load.delivery_window_end),
    )
