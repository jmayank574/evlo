"""Feasibility orchestration: turn a real load + truck into an auditable verdict.

Flow:
  1. Route the lane (real road distance + drive time) via a RoutingProvider.
  2. If charging is needed, find DC-fast chargers along the route corridor from
     the charging providers, with an approximate along-route distance for each.
  3. Decide which chargers are actually *reachable* on the current charge and
     pick the best (highest known power) as the charge stop.
  4. Run the pure energy model (assess) and persist a full, reproducible snapshot.

All providers are injected, so this is unit-tested with fakes (no network).

Honesty notes baked in:
  * A charger is only usable for the charge-time computation if its power (kW)
    is known. NREL/AFDC does not publish kW, so charge-plan power comes from OCM;
    we never invent a kW figure. NREL stations are still surfaced for the map.
  * Corridor sampling uses the route geometry; we subsample points to bound API
    calls (and respect quota). This coarseness is a stated v1 simplification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.adapters.base import (
    ChargingProvider,
    Coord,
    ProviderError,
    Route,
    RoutingProvider,
    StationResult,
)
from app.adapters.charging import haversine_mi
from app.domain.energy import Assessment as DomainAssessment
from app.domain.energy import ChargeOption, ModelParams, TruckSpec, assess
from app.models import Assessment, Load, Truck
from app.services.loads import resolve_windows

# Corridor search tuning (stated simplifications, surfaced in the methodology).
CORRIDOR_BUFFER_MI = 12.0      # how far off the route a charger may sit
CORRIDOR_MIN_GAP_MI = 60.0     # min spacing between sampled corridor points


@dataclass(frozen=True)
class CorridorCharger:
    station: StationResult
    along_route_mi: float


# --------------------------------------------------------------------------- #
# Pure helpers (no DB, no network)
# --------------------------------------------------------------------------- #


def cumulative_points(geometry: dict | None) -> list[tuple[Coord, float]]:
    """[(lat,lon), along_route_mi] for each vertex of a GeoJSON LineString."""
    if not geometry or "coordinates" not in geometry:
        return []
    pts: list[tuple[Coord, float]] = []
    cum = 0.0
    prev: Coord | None = None
    for lon, lat in geometry["coordinates"]:
        if prev is not None:
            cum += haversine_mi(prev[0], prev[1], lat, lon)
        pts.append(((lat, lon), cum))
        prev = (lat, lon)
    return pts


def subsample(points: list[tuple[Coord, float]], min_gap_mi: float) -> list[tuple[Coord, float]]:
    """Thin sampled points so corridor lookups don't hammer the charging APIs."""
    out: list[tuple[Coord, float]] = []
    last = float("-inf")
    for pt, along in points:
        if along - last >= min_gap_mi:
            out.append((pt, along))
            last = along
    if points and (not out or out[-1][1] != points[-1][1]):
        out.append(points[-1])
    return out


def find_corridor_chargers(
    providers: list[ChargingProvider],
    points: list[tuple[Coord, float]],
    buffer_mi: float,
) -> list[CorridorCharger]:
    """Collect unique chargers near the corridor, each tagged with the smallest
    along-route distance at which it was seen.

    Best-effort per (point, provider): a single provider timeout skips that one
    lookup (partial corridor coverage is already a stated simplification — we are
    NOT inventing chargers). But if EVERY lookup fails (a real outage), we fail
    loudly rather than return a misleadingly empty corridor.
    """
    found: dict[tuple[str, str], CorridorCharger] = {}
    attempts = 0
    failures = 0
    last_error: ProviderError | None = None
    for (lat, lon), along in points:
        for provider in providers:
            attempts += 1
            try:
                stations = provider.stations_near(lat, lon, buffer_mi)
            except ProviderError as exc:
                failures += 1
                last_error = exc
                continue
            for s in stations:
                key = (s.source, s.external_id)
                prev = found.get(key)
                if prev is None or along < prev.along_route_mi:
                    found[key] = CorridorCharger(station=s, along_route_mi=along)
    if attempts and failures == attempts and last_error is not None:
        raise last_error  # total outage -> fail loud, never fabricate a corridor
    return list(found.values())


def truck_to_spec(truck: Truck) -> TruckSpec:
    return TruckSpec(
        name=f"{truck.make} {truck.model}",
        usable_kwh=float(truck.usable_kwh),
        base_consumption_kwh_per_mi=float(truck.base_consumption_kwh_per_mi),
        reference_payload_lb=float(truck.reference_payload_lb),
        max_charge_kw=float(truck.max_charge_kw),
        gvwr_lb=float(truck.gvwr_lb),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


@dataclass
class LoadContext:
    """Route + corridor chargers + resolved time windows for a load, computed ONCE
    and reused across the whole fleet (all depend only on the load, not the truck).
    This is the efficiency win that makes fleet ranking cheap: one Mapbox route +
    one corridor scan + one window-resolution per load, not per truck."""

    route: Route
    corridor: list[CorridorCharger]
    options: list[ChargeOption]
    by_ref: dict[str, CorridorCharger]
    deliver_by: datetime
    pickup_start: datetime


def _ref(c: CorridorCharger) -> str:
    return f"{c.station.source}:{c.station.external_id}"


def build_load_context(
    load: Load,
    router: RoutingProvider,
    charging_providers: list[ChargingProvider],
    now: datetime | None = None,
) -> LoadContext:
    origin: Coord = (float(load.origin_lat), float(load.origin_lon))
    dest: Coord = (float(load.dest_lat), float(load.dest_lon))
    route = router.route(origin, dest)
    pts = subsample(cumulative_points(route.geometry), CORRIDOR_MIN_GAP_MI)
    corridor = find_corridor_chargers(charging_providers, pts, CORRIDOR_BUFFER_MI)
    by_ref = {_ref(c): c for c in corridor}
    options = [
        ChargeOption(ref=_ref(c), along_mi=c.along_route_mi, power_kw=c.station.max_power_kw or 0.0)
        for c in corridor
    ]
    w = resolve_windows(load, now)  # resolve relative offsets -> absolute at request time
    return LoadContext(
        route=route, corridor=corridor, options=options, by_ref=by_ref,
        deliver_by=w.delivery_end, pickup_start=w.pickup_start,
    )


@dataclass
class FeasibilityResult:
    domain: DomainAssessment
    route: Route
    by_ref: dict[str, CorridorCharger]
    deliver_by: datetime
    pickup_start: datetime


def assess_truck(
    *, truck: Truck, load: Load, soc_start_pct: float, params: ModelParams, ctx: LoadContext,
    time_mode: str = "arrive_by", depart_at: datetime | None = None, now: datetime | None = None,
) -> FeasibilityResult:
    """Run the energy model for one truck against a pre-built load context."""
    spec = truck_to_spec(truck)
    domain = assess(
        truck=spec,
        payload_lb=float(load.weight_lb),
        distance_mi=ctx.route.distance_mi,
        drive_hours=ctx.route.duration_hours,
        deliver_by=ctx.deliver_by,
        soc_start_pct=soc_start_pct,
        params=params,
        corridor=ctx.options,
        time_mode=time_mode,
        depart_at=depart_at,
        pickup_window_start=ctx.pickup_start,
        now=now,
    )
    return FeasibilityResult(
        domain=domain, route=ctx.route, by_ref=ctx.by_ref,
        deliver_by=ctx.deliver_by, pickup_start=ctx.pickup_start,
    )


def _D(x: float, places: str = "0.001") -> Decimal:
    return Decimal(str(x)).quantize(Decimal(places))


def _stop_snapshot(c: CorridorCharger, *, order: int, energy_added_kwh: float, charge_hours: float) -> dict:
    """A charge stop the truck actually makes — the lean, map-ready record. Only
    the picked stops are stored (not the full corridor of candidates), so each
    assessment stays small and the map shows exactly the plan."""
    s = c.station
    return {
        "order": order,
        "source": s.source,
        "external_id": s.external_id,
        "name": s.name,
        "network": s.network,
        "lat": s.lat,
        "lon": s.lon,
        "max_power_kw": s.max_power_kw,
        "num_dc_fast_ports": s.num_dc_fast_ports,
        "connector_types": s.connector_types,
        "along_route_mi": round(c.along_route_mi, 1),
        "energy_added_kwh": round(energy_added_kwh, 1),
        "charge_minutes": round(charge_hours * 60, 0),
        "picked": True,
    }


def persist_assessment(
    db: Session,
    *,
    truck: Truck,
    load: Load,
    soc_start_pct: float,
    params: ModelParams,
    result: FeasibilityResult,
) -> Assessment:
    """Write a fully reproducible audit record and return it."""
    d = result.domain
    chargers_used = []
    for order, stop in enumerate(d.stops, start=1):
        c = result.by_ref.get(stop.ref)
        if c is None:
            continue
        chargers_used.append(
            _stop_snapshot(
                c, order=order,
                energy_added_kwh=stop.energy_added_kwh, charge_hours=stop.charge_hours,
            )
        )

    truck_snapshot = {
        "id": str(truck.id), "make": truck.make, "model": truck.model, "variant": truck.variant,
        "usable_kwh": float(truck.usable_kwh),
        "base_consumption_kwh_per_mi": float(truck.base_consumption_kwh_per_mi),
        "reference_payload_lb": truck.reference_payload_lb,
        "max_charge_kw": float(truck.max_charge_kw), "gvwr_lb": truck.gvwr_lb,
        "provenance": truck.provenance,
    }
    load_snapshot = {
        "id": str(load.id), "reference": load.reference, "data_source": load.data_source,
        "origin_label": load.origin_label, "dest_label": load.dest_label,
        "weight_lb": load.weight_lb,
        # Resolved (absolute) windows actually used for this assessment.
        "pickup_window_start": result.pickup_start.isoformat(),
        "delivery_window_end": result.deliver_by.isoformat(),
    }

    row = Assessment(
        truck_id=truck.id,
        load_id=load.id,
        truck_snapshot=truck_snapshot,
        load_snapshot=load_snapshot,
        soc_start_pct=_D(soc_start_pct, "0.01"),
        reserve_pct=_D(params.reserve_pct, "0.01"),
        dwell_buffer_min=_D(params.dwell_buffer_min, "0.01"),
        payload_coefficient_kwh_per_mi_per_ton=_D(params.payload_coefficient_kwh_per_mi_per_ton, "0.0001"),
        charge_efficiency=_D(params.charge_efficiency, "0.001"),
        charge_soc_cap_pct=_D(params.charge_soc_cap_pct, "0.01"),
        energy_price_per_kwh_usd=Decimal(str(params.energy_price_per_kwh_usd)).quantize(Decimal("0.0001")),
        routing_provider=result.route.provider,
        route_distance_mi=_D(result.route.distance_mi, "0.01"),
        route_drive_hours=_D(result.route.duration_hours, "0.001"),
        route_geometry=result.route.geometry,
        chargers_used=chargers_used,
        verdict=d.verdict.value,
        reasons=list(d.reasons),
        consumption_kwh_per_mi=_D(d.consumption_kwh_per_mi, "0.001"),
        energy_required_kwh=_D(d.energy_required_kwh, "0.001"),
        usable_energy_for_trip_kwh=_D(d.usable_energy_for_trip_kwh, "0.001"),
        charging_required=d.charging_required,
        num_charge_stops=d.num_charge_stops,
        stranded_at_mi=_D(d.stranded_at_mi, "0.01") if d.stranded_at_mi is not None else None,
        energy_to_add_kwh=_D(d.energy_to_add_kwh, "0.001"),
        charge_time_hours=_D(d.charge_time_hours, "0.001"),
        charge_cost_usd=d.charge_cost_usd,
        total_hours=_D(d.total_hours, "0.001"),
        time_mode=d.time_mode,
        latest_departure=d.latest_departure,
        projected_arrival=d.projected_arrival,
        now_reference=d.now_reference,
        on_time=d.on_time,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_feasibility(
    db: Session,
    *,
    truck: Truck,
    load: Load,
    soc_start_pct: float,
    params: ModelParams,
    router: RoutingProvider,
    charging_providers: list[ChargingProvider],
    time_mode: str = "arrive_by",
    depart_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[Assessment, FeasibilityResult]:
    """Single-truck assessment (builds the load context, then assesses one truck)."""
    ctx = build_load_context(load, router, charging_providers, now=now)
    result = assess_truck(
        truck=truck, load=load, soc_start_pct=soc_start_pct, params=params, ctx=ctx,
        time_mode=time_mode, depart_at=depart_at, now=now,
    )
    row = persist_assessment(
        db, truck=truck, load=load, soc_start_pct=soc_start_pct, params=params, result=result
    )
    return row, result


def run_fleet(
    db: Session,
    *,
    load: Load,
    trucks: list[Truck],
    soc_start_pct: float,
    params: ModelParams,
    router: RoutingProvider,
    charging_providers: list[ChargingProvider],
    time_mode: str = "arrive_by",
    depart_at: datetime | None = None,
    now: datetime | None = None,
) -> list[Assessment]:
    """Assess every truck for one load against a SHARED route + corridor (one set
    of API calls), persist each, and return the assessment rows (unordered;
    ranking is applied at the API layer)."""
    ctx = build_load_context(load, router, charging_providers, now=now)
    rows: list[Assessment] = []
    for truck in trucks:
        result = assess_truck(
            truck=truck, load=load, soc_start_pct=soc_start_pct, params=params, ctx=ctx,
            time_mode=time_mode, depart_at=depart_at, now=now,
        )
        rows.append(
            persist_assessment(
                db, truck=truck, load=load, soc_start_pct=soc_start_pct, params=params, result=result
            )
        )
    return rows
