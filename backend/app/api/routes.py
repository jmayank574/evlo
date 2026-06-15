"""HTTP routes."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ProviderError
from app.api.deps import Providers, get_db, get_providers
from app.core.config import get_settings
from app.domain.energy import ARRIVE_BY, DEPART_AT, ModelParams
from app.models import Assessment, Load, Truck
from app.schemas import (
    AssessParams,
    AssessRequest,
    AssessmentOut,
    FleetRequest,
    FleetResponse,
    LoadOut,
    MethodologyOut,
    TruckOut,
)
from app.services.feasibility import run_feasibility, run_fleet
from app.services.loads import resolve_windows
from app.services.methodology import build_methodology

_VERDICT_RANK = {"feasible": 0, "feasible_with_charging": 1, "infeasible": 2}


def _aware(dt: datetime) -> datetime:
    """Coerce naive datetimes (SQLite drops tz) to UTC-aware for safe math."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_out(row: Assessment, load: Load, now: datetime) -> AssessmentOut:
    out = AssessmentOut.model_validate(row)
    w = resolve_windows(load, now)  # absolute windows for this request's date
    # Depart-at deciding number: slack before the delivery window closes.
    out.arrival_margin_min = round(
        (_aware(w.delivery_end) - _aware(row.projected_arrival)).total_seconds() / 60.0, 1
    )
    # Arrive-by deciding number: the dispatcher's runway = latest safe departure
    # minus NOW (anchored to real current time, not the pickup window — D20).
    # Negative once the latest departure has passed (time-infeasible).
    ref = _aware(row.now_reference) if row.now_reference else now
    out.departure_slack_min = round(
        (_aware(row.latest_departure) - ref).total_seconds() / 60.0, 1
    )
    return out


def _rank_key(out: AssessmentOut) -> tuple:
    # Best first: feasible < charging < infeasible; then fewer stops; then more
    # slack (mode-appropriate: departure slack in arrive-by, arrival margin in depart-at).
    if out.time_mode == ARRIVE_BY:
        slack = out.departure_slack_min if out.departure_slack_min is not None else -1e9
    else:
        slack = out.arrival_margin_min if out.arrival_margin_min is not None else -1e9
    return (_VERDICT_RANK.get(out.verdict, 99), out.num_charge_stops, -slack)

router = APIRouter(prefix="/api")


def provider_dep(db: Session = Depends(get_db)) -> Providers:
    return get_providers(db)


def build_params(ap: AssessParams | None) -> ModelParams:
    base = ModelParams()
    if ap is None:
        return base
    overrides: dict = {}
    for f in ("reserve_pct", "dwell_buffer_min", "payload_coefficient_kwh_per_mi_per_ton",
              "charge_efficiency", "charge_soc_cap_pct", "min_charger_power_kw"):
        v = getattr(ap, f)
        if v is not None:
            overrides[f] = v
    if ap.energy_price_per_kwh_usd is not None:
        overrides["energy_price_per_kwh_usd"] = Decimal(str(ap.energy_price_per_kwh_usd))
    return dataclasses.replace(base, **overrides)


@router.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    # HEAD included so uptime monitors (which default to HEAD) don't get a 405.
    return {"status": "ok"}


@router.get("/config")
def config() -> dict:
    # Public pk token only — safe to expose to the browser for map display.
    return {"mapbox_public_token": get_settings().mapbox_public_token}


@router.get("/trucks", response_model=list[TruckOut])
def list_trucks(db: Session = Depends(get_db)) -> list[Truck]:
    return list(db.scalars(select(Truck).order_by(Truck.make)).all())


@router.get("/loads", response_model=list[LoadOut])
def list_loads(db: Session = Depends(get_db)) -> list[LoadOut]:
    now = datetime.now(timezone.utc)
    out: list[LoadOut] = []
    for load in db.scalars(select(Load).order_by(Load.reference)).all():
        w = resolve_windows(load, now)  # relative offsets -> absolute against today
        out.append(
            LoadOut(
                id=load.id, reference=load.reference,
                origin_label=load.origin_label, origin_lat=float(load.origin_lat), origin_lon=float(load.origin_lon),
                dest_label=load.dest_label, dest_lat=float(load.dest_lat), dest_lon=float(load.dest_lon),
                weight_lb=load.weight_lb,
                pickup_window_start=w.pickup_start, pickup_window_end=w.pickup_end,
                delivery_window_start=w.delivery_start, delivery_window_end=w.delivery_end,
                data_source=load.data_source,
            )
        )
    return out


@router.get("/methodology", response_model=MethodologyOut)
def methodology() -> MethodologyOut:
    return build_methodology()


def _validate_mode(time_mode: str, depart_at: datetime | None) -> None:
    if time_mode not in (ARRIVE_BY, DEPART_AT):
        raise HTTPException(status_code=422, detail=f"time_mode must be '{ARRIVE_BY}' or '{DEPART_AT}'")
    if time_mode == DEPART_AT and depart_at is None:
        raise HTTPException(status_code=422, detail="depart_at is required when time_mode is 'depart_at'")


@router.get("/assessments/{assessment_id}", response_model=AssessmentOut)
def get_assessment(assessment_id: uuid.UUID, db: Session = Depends(get_db)) -> AssessmentOut:
    row = db.get(Assessment, assessment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if row.load is not None:
        return _to_out(row, row.load, datetime.now(timezone.utc))
    return AssessmentOut.model_validate(row)


@router.post("/assess", response_model=AssessmentOut)
def assess_load(
    req: AssessRequest,
    db: Session = Depends(get_db),
    providers: Providers = Depends(provider_dep),
) -> AssessmentOut:
    _validate_mode(req.time_mode, req.depart_at)
    truck = db.get(Truck, req.truck_id)
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    load = db.get(Load, req.load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")

    params = build_params(req.params)
    now = datetime.now(timezone.utc)
    depart_at = _aware(req.depart_at) if req.depart_at else None
    try:
        row, _ = run_feasibility(
            db, truck=truck, load=load, soc_start_pct=req.soc_start_pct,
            params=params, router=providers.router, charging_providers=providers.charging,
            time_mode=req.time_mode, depart_at=depart_at, now=now,
        )
    except ProviderError as exc:
        # Fail loudly — never fabricate routing/charger data to fill a gap.
        raise HTTPException(status_code=502, detail=f"Upstream data provider failed: {exc}") from exc
    return _to_out(row, load, now)


@router.post("/assess/fleet", response_model=FleetResponse)
def assess_fleet(
    req: FleetRequest,
    db: Session = Depends(get_db),
    providers: Providers = Depends(provider_dep),
) -> FleetResponse:
    """Assess every truck for one load (shared route + corridor) and rank them
    best-option-first. This is the core decision view."""
    _validate_mode(req.time_mode, req.depart_at)
    load = db.get(Load, req.load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    trucks = list(db.scalars(select(Truck).order_by(Truck.make)).all())
    if not trucks:
        raise HTTPException(status_code=404, detail="No trucks in fleet")

    params = build_params(req.params)
    now = datetime.now(timezone.utc)
    depart_at = _aware(req.depart_at) if req.depart_at else None
    try:
        rows = run_fleet(
            db, load=load, trucks=trucks, soc_start_pct=req.soc_start_pct,
            params=params, router=providers.router, charging_providers=providers.charging,
            time_mode=req.time_mode, depart_at=depart_at, now=now,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream data provider failed: {exc}") from exc

    items = sorted((_to_out(r, load, now) for r in rows), key=_rank_key)
    return FleetResponse(load_id=load.id, items=items)
