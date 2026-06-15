"""Pydantic request/response schemas for the API."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TruckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    make: str
    model: str
    variant: str
    usable_kwh: float
    published_range_mi: int
    gvwr_lb: int
    max_charge_kw: float
    base_consumption_kwh_per_mi: float
    reference_payload_lb: int
    spec_source_url: str
    spec_accessed_date: date
    provenance: dict


class LoadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reference: str
    origin_label: str
    origin_lat: float
    origin_lon: float
    dest_label: str
    dest_lat: float
    dest_lon: float
    weight_lb: int
    pickup_window_start: datetime
    pickup_window_end: datetime
    delivery_window_start: datetime
    delivery_window_end: datetime
    data_source: str  # "synthetic" — UI badges this


class AssessParams(BaseModel):
    """Optional overrides for the model knobs (all default to ModelParams).
    Surfaced in the UI so a user can turn a knob and watch the verdict change.
    """
    reserve_pct: float | None = None
    dwell_buffer_min: float | None = None
    payload_coefficient_kwh_per_mi_per_ton: float | None = None
    charge_efficiency: float | None = None
    charge_soc_cap_pct: float | None = None
    min_charger_power_kw: float | None = None
    energy_price_per_kwh_usd: float | None = None


class AssessRequest(BaseModel):
    load_id: uuid.UUID
    truck_id: uuid.UUID
    soc_start_pct: float = Field(ge=0, le=100)
    params: AssessParams | None = None


class FleetRequest(BaseModel):
    """Assess the whole fleet for one load, at one (fleet-wide) starting SoC."""

    load_id: uuid.UUID
    soc_start_pct: float = Field(ge=0, le=100)
    params: AssessParams | None = None


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    verdict: str
    reasons: list[str]
    # energy
    consumption_kwh_per_mi: float
    energy_required_kwh: float
    usable_energy_for_trip_kwh: float
    charging_required: bool
    num_charge_stops: int
    energy_to_add_kwh: float
    charge_time_hours: float
    charge_cost_usd: float
    # Set by the API: minutes of slack before the delivery window closes
    # (negative = late). The "deciding number" for the fleet ranking.
    arrival_margin_min: float | None = None
    # time
    route_distance_mi: float
    route_drive_hours: float
    total_hours: float
    projected_arrival: datetime
    on_time: bool
    routing_provider: str
    # detail
    route_geometry: dict | None
    chargers_used: list[dict]
    soc_start_pct: float
    # params actually used (audit)
    reserve_pct: float
    dwell_buffer_min: float
    payload_coefficient_kwh_per_mi_per_ton: float
    charge_efficiency: float
    charge_soc_cap_pct: float
    energy_price_per_kwh_usd: float
    truck_snapshot: dict
    load_snapshot: dict
    created_at: datetime


class FleetResponse(BaseModel):
    """Trucks assessed for one load, ranked best option first."""

    load_id: uuid.UUID
    items: list[AssessmentOut]


class ParamDoc(BaseModel):
    name: str
    value: float
    unit: str
    description: str
    is_estimate: bool


class SourceDoc(BaseModel):
    name: str
    url: str
    used_for: str
    trust: str


class MethodologyOut(BaseModel):
    params: list[ParamDoc]
    sources: list[SourceDoc]
    notes: list[str]
