"""SQLAlchemy models for Voltpath.

Provenance discipline is baked into the schema:
  * ``Truck.provenance`` is a per-field JSONB trust map (a single truck can mix
    'manufacturer', 'secondary', 'derived', 'assumption' trust across fields).
  * ``Load.data_source`` defaults to 'synthetic' and is the ONLY table allowed
    to hold invented data; it is indexed so the UI can filter/badge it.
  * ``ChargingStation`` carries ``data_source`` (NREL/OCM) + ``fetched_at`` +
    ``expires_at`` per row, so cached chargers are provenance-stamped and
    expirable, never permanent truth.
  * ``Assessment`` snapshots the full input (truck, load, every ModelParams
    value, route, charger set used) alongside the verdict, so any past
    assessment is reproducible. Money/energy are Numeric; PKs are UUID.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONBType

# Allowed enumerated values, enforced with CHECK constraints (portable, and
# avoids rigid native-enum migrations).
TRUST_LEVELS = ("manufacturer", "regulatory", "measured", "secondary", "derived", "assumption")
LOAD_SOURCES = ("synthetic", "real")
STATION_SOURCES = ("NREL", "OCM")
VERDICTS = ("feasible", "feasible_with_charging", "infeasible")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Truck(Base):
    __tablename__ = "trucks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    make: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    variant: Mapped[str] = mapped_column(String(120))

    usable_kwh: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    published_range_mi: Mapped[int] = mapped_column(Integer)
    gvwr_lb: Mapped[int] = mapped_column(Integer)
    max_charge_kw: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    # Derived from usable_kwh / published_range; trust='derived' in provenance.
    base_consumption_kwh_per_mi: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    reference_payload_lb: Mapped[int] = mapped_column(Integer)

    spec_source_url: Mapped[str] = mapped_column(String(500))
    spec_accessed_date: Mapped[date] = mapped_column(Date)
    # Per-field trust map: {field: {trust, source_url, accessed_date, note?}}
    provenance: Mapped[dict] = mapped_column(JSONBType, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    assessments: Mapped[list["Assessment"]] = relationship(back_populates="truck")


class Load(Base):
    __tablename__ = "loads"
    __table_args__ = (
        CheckConstraint(
            f"data_source IN {LOAD_SOURCES}", name="ck_loads_data_source"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    reference: Mapped[str] = mapped_column(String(40), unique=True)

    origin_label: Mapped[str] = mapped_column(String(160))
    origin_lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    origin_lon: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    dest_label: Mapped[str] = mapped_column(String(160))
    dest_lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    dest_lon: Mapped[Decimal] = mapped_column(Numeric(9, 6))

    weight_lb: Mapped[int] = mapped_column(Integer)
    pickup_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # The only synthetic data in the system. Indexed so the UI can filter/badge.
    data_source: Mapped[str] = mapped_column(
        String(16), default="synthetic", index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    assessments: Mapped[list["Assessment"]] = relationship(back_populates="load")


class ChargingStation(Base):
    __tablename__ = "charging_stations"
    __table_args__ = (
        UniqueConstraint("data_source", "external_id", name="uq_station_source_extid"),
        CheckConstraint(
            f"data_source IN {STATION_SOURCES}", name="ck_station_data_source"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    data_source: Mapped[str] = mapped_column(String(8))  # NREL | OCM
    external_id: Mapped[str] = mapped_column(String(80))
    network: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    num_dc_fast_ports: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    connector_types: Mapped[list | None] = mapped_column(JSONBType, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(f"verdict IN {VERDICTS}", name="ck_assessment_verdict"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()

    # Convenience FKs (nullable) + denormalized snapshots for reproducibility
    # that survives later edits/deletes of the referenced rows.
    truck_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True
    )
    load_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("loads.id", ondelete="SET NULL"), nullable=True
    )
    truck_snapshot: Mapped[dict] = mapped_column(JSONBType)
    load_snapshot: Mapped[dict] = mapped_column(JSONBType)

    soc_start_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))

    # --- ModelParams snapshot: typed columns, fully queryable ---
    reserve_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    dwell_buffer_min: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    payload_coefficient_kwh_per_mi_per_ton: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    charge_efficiency: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    charge_soc_cap_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    energy_price_per_kwh_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4))

    # --- Route + chargers used (provenance-stamped snapshots) ---
    routing_provider: Mapped[str] = mapped_column(String(40))
    route_distance_mi: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    route_drive_hours: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    route_geometry: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    chargers_used: Mapped[list] = mapped_column(JSONBType, default=list)

    # --- Verdict + outputs ---
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    reasons: Mapped[list] = mapped_column(JSONBType, default=list)
    consumption_kwh_per_mi: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    energy_required_kwh: Mapped[Decimal] = mapped_column(Numeric(9, 3))
    usable_energy_for_trip_kwh: Mapped[Decimal] = mapped_column(Numeric(9, 3))
    charging_required: Mapped[bool] = mapped_column(Boolean)
    energy_to_add_kwh: Mapped[Decimal] = mapped_column(Numeric(9, 3))
    charge_time_hours: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    charge_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total_hours: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    projected_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    on_time: Mapped[bool] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    truck: Mapped["Truck | None"] = relationship(back_populates="assessments")
    load: Mapped["Load | None"] = relationship(back_populates="assessments")


__all__ = [
    "Base",
    "Truck",
    "Load",
    "ChargingStation",
    "Assessment",
    "TRUST_LEVELS",
    "LOAD_SOURCES",
    "STATION_SOURCES",
    "VERDICTS",
]
