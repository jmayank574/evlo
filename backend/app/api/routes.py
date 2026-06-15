"""HTTP routes."""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ProviderError
from app.api.deps import Providers, get_db, get_providers
from app.core.config import get_settings
from app.domain.energy import ModelParams
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
from app.services.methodology import build_methodology

_VERDICT_RANK = {"feasible": 0, "feasible_with_charging": 1, "infeasible": 2}


def _to_out(row: Assessment, load: Load) -> AssessmentOut:
    out = AssessmentOut.model_validate(row)
    margin = (load.delivery_window_end - row.projected_arrival).total_seconds() / 60.0
    out.arrival_margin_min = round(margin, 1)
    return out


def _rank_key(out: AssessmentOut) -> tuple:
    # Best first: feasible < charging < infeasible; then fewer stops; then more
    # arrival slack (larger margin first).
    margin = out.arrival_margin_min if out.arrival_margin_min is not None else -1e9
    return (_VERDICT_RANK.get(out.verdict, 99), out.num_charge_stops, -margin)

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


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config")
def config() -> dict:
    # Public pk token only — safe to expose to the browser for map display.
    return {"mapbox_public_token": get_settings().mapbox_public_token}


@router.get("/trucks", response_model=list[TruckOut])
def list_trucks(db: Session = Depends(get_db)) -> list[Truck]:
    return list(db.scalars(select(Truck).order_by(Truck.make)).all())


@router.get("/loads", response_model=list[LoadOut])
def list_loads(db: Session = Depends(get_db)) -> list[Load]:
    return list(db.scalars(select(Load).order_by(Load.reference)).all())


@router.get("/methodology", response_model=MethodologyOut)
def methodology() -> MethodologyOut:
    return build_methodology()


@router.get("/assessments/{assessment_id}", response_model=AssessmentOut)
def get_assessment(assessment_id: uuid.UUID, db: Session = Depends(get_db)) -> AssessmentOut:
    row = db.get(Assessment, assessment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    out = AssessmentOut.model_validate(row)
    if row.load is not None:
        out.arrival_margin_min = round(
            (row.load.delivery_window_end - row.projected_arrival).total_seconds() / 60.0, 1
        )
    return out


@router.post("/assess", response_model=AssessmentOut)
def assess_load(
    req: AssessRequest,
    db: Session = Depends(get_db),
    providers: Providers = Depends(provider_dep),
) -> AssessmentOut:
    truck = db.get(Truck, req.truck_id)
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    load = db.get(Load, req.load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")

    params = build_params(req.params)
    try:
        row, _ = run_feasibility(
            db, truck=truck, load=load, soc_start_pct=req.soc_start_pct,
            params=params, router=providers.router, charging_providers=providers.charging,
        )
    except ProviderError as exc:
        # Fail loudly — never fabricate routing/charger data to fill a gap.
        raise HTTPException(status_code=502, detail=f"Upstream data provider failed: {exc}") from exc
    return _to_out(row, load)


@router.post("/assess/fleet", response_model=FleetResponse)
def assess_fleet(
    req: FleetRequest,
    db: Session = Depends(get_db),
    providers: Providers = Depends(provider_dep),
) -> FleetResponse:
    """Assess every truck for one load (shared route + corridor) and rank them
    best-option-first. This is the core decision view."""
    load = db.get(Load, req.load_id)
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found")
    trucks = list(db.scalars(select(Truck).order_by(Truck.make)).all())
    if not trucks:
        raise HTTPException(status_code=404, detail="No trucks in fleet")

    params = build_params(req.params)
    try:
        rows = run_fleet(
            db, load=load, trucks=trucks, soc_start_pct=req.soc_start_pct,
            params=params, router=providers.router, charging_providers=providers.charging,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream data provider failed: {exc}") from exc

    items = sorted((_to_out(r, load) for r in rows), key=_rank_key)
    return FleetResponse(load_id=load.id, items=items)
