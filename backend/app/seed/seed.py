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
    dict(
        make="Tesla",
        model="Semi",
        variant="Standard Range (548 kWh)",
        usable_kwh=Decimal("548.00"),
        published_range_mi=325,
        gvwr_lb=82_000,
        max_charge_kw=Decimal("1200.00"),
        base_consumption_kwh_per_mi=Decimal("1.686"),  # 548 / 325
        reference_payload_lb=40_000,
        spec_source_url=TESLA_CARB_URL,
        provenance={
            "usable_kwh": _prov("regulatory", TESLA_CARB_URL, "548 kWh Standard Range trim per May 2026 CARB filing"),
            "published_range_mi": _prov("secondary", TESLA_CARB_URL, "~325 mi per reporting of the CARB filing (approximate; not a manufacturer spec sheet)"),
            "gvwr_lb": _prov("regulatory", TESLA_CARB_URL),
            "max_charge_kw": _prov("manufacturer", TESLA_SPEC_URL, "up to 1.2 MW Megacharger (shared Semi platform)"),
            "base_consumption_kwh_per_mi": _prov("derived", TESLA_CARB_URL, "derived as usable_kwh / published_range (548/325 = 1.686)"),
            "reference_payload_lb": _prov("assumption", TESLA_CARB_URL, REFERENCE_PAYLOAD_NOTE),
        },
    ),
]


# Synthetic loads carry RELATIVE date offsets, not absolute dates, so they never
# go stale: deadlines resolve to "today + N days" at request time. Spec tuple:
# (ref, origin, o_lat, o_lon, dest, d_lat, d_lon, weight_lb, day_offset,
#  (pickup_h0, pickup_h1), (delivery_h0, delivery_h1))  -- hours are UTC wall-clock.
_LOAD_SPECS = [
    ("LD-1001", "Los Angeles, CA", 34.0522, -118.2437, "Oakland, CA", 37.8044, -122.2712, 42_000, 1, (6, 9), (16, 20)),
    ("LD-1002", "Dallas, TX", 32.7767, -96.7970, "Houston, TX", 29.7604, -95.3698, 38_000, 1, (7, 9), (12, 15)),
    ("LD-1003", "Los Angeles, CA", 34.0522, -118.2437, "Sacramento, CA", 38.5816, -121.4944, 30_000, 2, (5, 8), (15, 19)),
    ("LD-1004", "San Diego, CA", 32.7157, -117.1611, "Los Angeles, CA", 34.0522, -118.2437, 24_000, 2, (8, 10), (12, 14)),
    ("LD-1005", "Fort Worth, TX", 32.7555, -97.3308, "San Antonio, TX", 29.4241, -98.4936, 44_000, 3, (6, 9), (11, 14)),
    ("LD-1006", "Long Beach, CA", 33.7542, -118.2165, "Bakersfield, CA", 35.3733, -119.0187, 36_000, 3, (7, 9), (12, 15)),
    ("LD-1007", "Austin, TX", 30.2672, -97.7431, "Houston, TX", 29.7604, -95.3698, 28_000, 4, (6, 8), (10, 13)),
    ("LD-1008", "Fresno, CA", 36.7378, -119.7871, "Los Angeles, CA", 34.0522, -118.2437, 40_000, 4, (5, 8), (13, 17)),
]


def _build_loads() -> list[dict]:
    out: list[dict] = []
    for ref, ol, olat, olon, dl, dlat, dlon, w, day, (p0, p1), (d0, d1) in _LOAD_SPECS:
        out.append(dict(
            reference=ref,
            origin_label=ol, origin_lat=Decimal(str(olat)), origin_lon=Decimal(str(olon)),
            dest_label=dl, dest_lat=Decimal(str(dlat)), dest_lon=Decimal(str(dlon)),
            weight_lb=w,
            # Absolute windows are left None; offsets are the source of truth.
            pickup_window_start=None, pickup_window_end=None,
            delivery_window_start=None, delivery_window_end=None,
            pickup_start_offset_h=Decimal(day * 24 + p0), pickup_end_offset_h=Decimal(day * 24 + p1),
            delivery_start_offset_h=Decimal(day * 24 + d0), delivery_end_offset_h=Decimal(day * 24 + d1),
        ))
    return out


LOADS: list[dict] = _build_loads()


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
