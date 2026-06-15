"""Idempotent seed data.

Two kinds of rows, with very different provenance discipline:

* TRUCKS are REAL. Each spec field carries a per-field trust label in
  ``provenance`` (manufacturer / regulatory / secondary / derived / assumption),
  so the UI never presents a derived or assumed number as a measured fact. See
  DATA_SOURCES.md for the citations behind every value here.

* LOADS are the ONLY synthetic data in the system. Every row is
  ``data_source='synthetic'`` so the UI badges it and it can be swapped for a
  real CSV later. Real city coordinates are used so routing is genuine; only the
  freight assignments are invented.

Run:  python -m app.seed.seed
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Load, Truck

ACCESSED = "2026-06-14"
ACCESSED_DATE = date(2026, 6, 14)


def _prov(trust: str, url: str, note: str | None = None) -> dict:
    d = {"trust": trust, "source_url": url, "accessed_date": ACCESSED}
    if note:
        d["note"] = note
    return d

# --------------------------------------------------------------------------- #
# Real trucks (cited). base_consumption = usable_kwh / published_range (derived);
# reference_payload_lb is an explicit modeling assumption (flagged), since OEMs
# do not publish the exact payload their range rating assumes.
# --------------------------------------------------------------------------- #

FREIGHTLINER_URL = "https://www.freightliner.com/trucks/ecascadia/specifications/"
VOLVO_URL = "https://www.volvotrucks.us/trucks/vnr-electric/"
VOLVO_SECONDARY_URL = "https://electrek.co/2022/01/14/volvo-trucks-introduces-second-generation-vnr-electric-with-bigger-battery-added-range-and-new-configurations/"
TESLA_CARB_URL = "https://electrek.co/2026/05/08/tesla-semi-battery-size-822-kwh-548-kwh-carb-official/"
TESLA_SPEC_URL = "https://www.teslasemi.com/specs"
NACFE_URL = "https://nacfe.org/research/run-on-less/run-on-less-electric-depot/"

REFERENCE_PAYLOAD_NOTE = (
    "OEMs do not publish the exact payload their range rating assumes; modeled "
    "as a conservative 40,000 lb cargo assumption. Tune the payload coefficient "
    "and this value in the Methodology panel."
)

TRUCKS: list[dict] = [
    dict(
        make="Freightliner",
        model="eCascadia",
        variant="Tandem drive, 438 kWh",
        usable_kwh=Decimal("438.00"),
        published_range_mi=220,
        gvwr_lb=82_000,
        max_charge_kw=Decimal("270.00"),
        base_consumption_kwh_per_mi=Decimal("1.991"),  # 438 / 220
        reference_payload_lb=40_000,
        spec_source_url=FREIGHTLINER_URL,
        provenance={
            "usable_kwh": _prov("manufacturer", FREIGHTLINER_URL),
            "published_range_mi": _prov("manufacturer", FREIGHTLINER_URL),
            "gvwr_lb": _prov("manufacturer", FREIGHTLINER_URL),
            "max_charge_kw": _prov("manufacturer", FREIGHTLINER_URL, "270 kW dual-port"),
            "base_consumption_kwh_per_mi": _prov("derived", FREIGHTLINER_URL, "usable_kwh / published_range"),
            "reference_payload_lb": _prov("assumption", FREIGHTLINER_URL, REFERENCE_PAYLOAD_NOTE),
        },
    ),
    dict(
        make="Volvo",
        model="VNR Electric",
        variant="6-battery (565 kWh installed)",
        usable_kwh=Decimal("452.00"),
        published_range_mi=275,
        gvwr_lb=82_000,
        max_charge_kw=Decimal("250.00"),
        base_consumption_kwh_per_mi=Decimal("1.644"),  # 452 / 275
        reference_payload_lb=40_000,
        spec_source_url=VOLVO_URL,
        provenance={
            "usable_kwh": _prov("secondary", VOLVO_SECONDARY_URL, "452 kWh usable per Electrek; official usable figure not published"),
            "published_range_mi": _prov("manufacturer", VOLVO_URL),
            "gvwr_lb": _prov("manufacturer", VOLVO_URL, "up to 82,000 lb GCW (6x2 tractor)"),
            "max_charge_kw": _prov("manufacturer", VOLVO_URL, "250 kW CCS1"),
            "base_consumption_kwh_per_mi": _prov("derived", VOLVO_URL, "usable_kwh / published_range"),
            "reference_payload_lb": _prov("assumption", VOLVO_URL, REFERENCE_PAYLOAD_NOTE),
        },
    ),
    dict(
        make="Tesla",
        model="Semi",
        variant="Long Range (822 kWh)",
        usable_kwh=Decimal("822.00"),
        published_range_mi=500,
        gvwr_lb=82_000,
        max_charge_kw=Decimal("1200.00"),
        base_consumption_kwh_per_mi=Decimal("1.644"),  # 822 / 500; cross-checks NACFE 1.55-1.72
        reference_payload_lb=40_000,
        spec_source_url=TESLA_SPEC_URL,
        provenance={
            "usable_kwh": _prov("regulatory", TESLA_CARB_URL, "822 kWh USABLE per May 2026 CARB filing (Long Range trim; the filing's 548 kWh is the separate Standard Range trim, not this truck's nameplate)"),
            "published_range_mi": _prov("manufacturer", TESLA_SPEC_URL, "500 mi at 82,000 lb GCW"),
            "gvwr_lb": _prov("regulatory", TESLA_CARB_URL),
            "max_charge_kw": _prov("manufacturer", TESLA_SPEC_URL, "up to 1.2 MW Megacharger"),
            "base_consumption_kwh_per_mi": _prov("derived", TESLA_SPEC_URL, "derived as usable_kwh / published_range (822/500 = 1.644); corroborated by NACFE measured 1.55-1.72 kWh/mi"),
            "reference_payload_lb": _prov("assumption", TESLA_SPEC_URL, REFERENCE_PAYLOAD_NOTE),
        },
    ),
]


def _dt(y: int, mo: int, d: int, h: int) -> datetime:
    return datetime(y, mo, d, h, 0, tzinfo=timezone.utc)


# Real city coordinates; only the freight assignment is synthetic.
LOADS: list[dict] = [
    dict(reference="LD-1001", origin_label="Los Angeles, CA", origin_lat=Decimal("34.052200"), origin_lon=Decimal("-118.243700"),
         dest_label="Oakland, CA", dest_lat=Decimal("37.804400"), dest_lon=Decimal("-122.271200"),
         weight_lb=42_000, pickup_window_start=_dt(2026, 6, 15, 6), pickup_window_end=_dt(2026, 6, 15, 9),
         delivery_window_start=_dt(2026, 6, 15, 16), delivery_window_end=_dt(2026, 6, 15, 20)),
    dict(reference="LD-1002", origin_label="Dallas, TX", origin_lat=Decimal("32.776700"), origin_lon=Decimal("-96.797000"),
         dest_label="Houston, TX", dest_lat=Decimal("29.760400"), dest_lon=Decimal("-95.369800"),
         weight_lb=38_000, pickup_window_start=_dt(2026, 6, 15, 7), pickup_window_end=_dt(2026, 6, 15, 9),
         delivery_window_start=_dt(2026, 6, 15, 12), delivery_window_end=_dt(2026, 6, 15, 15)),
    dict(reference="LD-1003", origin_label="Los Angeles, CA", origin_lat=Decimal("34.052200"), origin_lon=Decimal("-118.243700"),
         dest_label="Sacramento, CA", dest_lat=Decimal("38.581600"), dest_lon=Decimal("-121.494400"),
         weight_lb=30_000, pickup_window_start=_dt(2026, 6, 16, 5), pickup_window_end=_dt(2026, 6, 16, 8),
         delivery_window_start=_dt(2026, 6, 16, 15), delivery_window_end=_dt(2026, 6, 16, 19)),
    dict(reference="LD-1004", origin_label="San Diego, CA", origin_lat=Decimal("32.715700"), origin_lon=Decimal("-117.161100"),
         dest_label="Los Angeles, CA", dest_lat=Decimal("34.052200"), dest_lon=Decimal("-118.243700"),
         weight_lb=24_000, pickup_window_start=_dt(2026, 6, 16, 8), pickup_window_end=_dt(2026, 6, 16, 10),
         delivery_window_start=_dt(2026, 6, 16, 12), delivery_window_end=_dt(2026, 6, 16, 14)),
    dict(reference="LD-1005", origin_label="Fort Worth, TX", origin_lat=Decimal("32.755500"), origin_lon=Decimal("-97.330800"),
         dest_label="San Antonio, TX", dest_lat=Decimal("29.424100"), dest_lon=Decimal("-98.493600"),
         weight_lb=44_000, pickup_window_start=_dt(2026, 6, 17, 6), pickup_window_end=_dt(2026, 6, 17, 9),
         delivery_window_start=_dt(2026, 6, 17, 11), delivery_window_end=_dt(2026, 6, 17, 14)),
    dict(reference="LD-1006", origin_label="Long Beach, CA", origin_lat=Decimal("33.754200"), origin_lon=Decimal("-118.216500"),
         dest_label="Bakersfield, CA", dest_lat=Decimal("35.373300"), dest_lon=Decimal("-119.018700"),
         weight_lb=36_000, pickup_window_start=_dt(2026, 6, 17, 7), pickup_window_end=_dt(2026, 6, 17, 9),
         delivery_window_start=_dt(2026, 6, 17, 12), delivery_window_end=_dt(2026, 6, 17, 15)),
    dict(reference="LD-1007", origin_label="Austin, TX", origin_lat=Decimal("30.267200"), origin_lon=Decimal("-97.743100"),
         dest_label="Houston, TX", dest_lat=Decimal("29.760400"), dest_lon=Decimal("-95.369800"),
         weight_lb=28_000, pickup_window_start=_dt(2026, 6, 18, 6), pickup_window_end=_dt(2026, 6, 18, 8),
         delivery_window_start=_dt(2026, 6, 18, 10), delivery_window_end=_dt(2026, 6, 18, 13)),
    dict(reference="LD-1008", origin_label="Fresno, CA", origin_lat=Decimal("36.737800"), origin_lon=Decimal("-119.787100"),
         dest_label="Los Angeles, CA", dest_lat=Decimal("34.052200"), dest_lon=Decimal("-118.243700"),
         weight_lb=40_000, pickup_window_start=_dt(2026, 6, 18, 5), pickup_window_end=_dt(2026, 6, 18, 8),
         delivery_window_start=_dt(2026, 6, 18, 13), delivery_window_end=_dt(2026, 6, 18, 17)),
]


def seed_all(db: Session) -> tuple[int, int]:
    """Idempotent upsert. Returns (trucks_written, loads_written)."""
    trucks_written = 0
    for t in TRUCKS:
        t.setdefault("spec_accessed_date", ACCESSED_DATE)
        existing = db.scalar(
            select(Truck).where(
                Truck.make == t["make"], Truck.model == t["model"], Truck.variant == t["variant"]
            )
        )
        if existing is None:
            db.add(Truck(**t))
            trucks_written += 1
        else:
            for k, v in t.items():
                setattr(existing, k, v)

    loads_written = 0
    for ld in LOADS:
        existing = db.scalar(select(Load).where(Load.reference == ld["reference"]))
        if existing is None:
            db.add(Load(data_source="synthetic", **ld))
            loads_written += 1
        else:
            for k, v in ld.items():
                setattr(existing, k, v)
            existing.data_source = "synthetic"

    db.commit()
    return trucks_written, loads_written


def main() -> None:
    with SessionLocal() as db:
        trucks_written, loads_written = seed_all(db)
    print(f"Seeded: {trucks_written} new trucks, {loads_written} new synthetic loads.")


if __name__ == "__main__":
    main()
