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

from app.adapters.base import ChargingProvider, Coord, RoutingProvider, Route, StationResult
from app.adapters.charging import haversine_mi
from app.domain.energy import Assessment as DomainAssessment
from app.domain.energy import ModelParams, TruckSpec, assess
from app.models import Assessment, Load, Truck

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
    along-route distance at which it was seen."""
    found: dict[tuple[str, str], CorridorCharger] = {}
    for (lat, lon), along in points:
        for provider in providers:
            for s in provider.stations_near(lat, lon, buffer_mi):
                key = (s.source, s.external_id)
                prev = found.get(key)
                if prev is None or along < prev.along_route_mi:
                    found[key] = CorridorCharger(station=s, along_route_mi=along)
    return list(found.values())


def pick_charge_stop(
    chargers: list[CorridorCharger], reachable_distance_mi: float
) -> CorridorCharger | None:
    """Best (highest known kW) charger reachable before the truck hits reserve."""
    usable = [
        c for c in chargers
        if c.station.max_power_kw and c.along_route_mi <= reachable_distance_mi
    ]
    if not usable:
        return None
    return max(usable, key=lambda c: c.station.max_power_kw or 0.0)


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
class FeasibilityResult:
    domain: DomainAssessment
    route: Route
    charge_stop: CorridorCharger | None
    corridor: list[CorridorCharger]


def evaluate(
    *,
    truck: Truck,
    load: Load,
    soc_start_pct: float,
    params: ModelParams,
    router: RoutingProvider,
    charging_providers: list[ChargingProvider],
) -> FeasibilityResult:
    """Route, find corridor chargers if needed, and run the energy model."""
    spec = truck_to_spec(truck)
    origin: Coord = (float(load.origin_lat), float(load.origin_lon))
    dest: Coord = (float(load.dest_lat), float(load.dest_lon))

    route = router.route(origin, dest)

    from app.domain.energy import (
        consumption_kwh_per_mi,
        energy_required_kwh,
        usable_energy_for_trip_kwh,
    )

    consumption = consumption_kwh_per_mi(spec, float(load.weight_lb), params)
    energy_req = energy_required_kwh(spec, float(load.weight_lb), route.distance_mi, params)
    usable = usable_energy_for_trip_kwh(spec, soc_start_pct, params)

    charge_stop: CorridorCharger | None = None
    corridor: list[CorridorCharger] = []

    if energy_req > usable:
        pts = subsample(cumulative_points(route.geometry), CORRIDOR_MIN_GAP_MI)
        corridor = find_corridor_chargers(charging_providers, pts, CORRIDOR_BUFFER_MI)
        reachable_distance = usable / consumption if consumption > 0 else 0.0
        charge_stop = pick_charge_stop(corridor, reachable_distance)

    domain = assess(
        truck=spec,
        payload_lb=float(load.weight_lb),
        distance_mi=route.distance_mi,
        drive_hours=route.duration_hours,
        depart_at=load.pickup_window_start,
        deliver_by=load.delivery_window_end,
        soc_start_pct=soc_start_pct,
        params=params,
        charger_max_kw=charge_stop.station.max_power_kw if charge_stop else None,
        charger_reachable=charge_stop is not None,
    )
    return FeasibilityResult(domain=domain, route=route, charge_stop=charge_stop, corridor=corridor)


def _D(x: float, places: str = "0.001") -> Decimal:
    return Decimal(str(x)).quantize(Decimal(places))


def _charger_snapshot(c: CorridorCharger, picked: bool) -> dict:
    s = c.station
    return {
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
        "picked": picked,
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
    picked_key = (
        (result.charge_stop.station.source, result.charge_stop.station.external_id)
        if result.charge_stop else None
    )
    chargers_used = [
        _charger_snapshot(
            c, picked=(c.station.source, c.station.external_id) == picked_key
        )
        for c in result.corridor
    ]

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
        "pickup_window_start": load.pickup_window_start.isoformat(),
        "delivery_window_end": load.delivery_window_end.isoformat(),
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
        energy_to_add_kwh=_D(d.energy_to_add_kwh, "0.001"),
        charge_time_hours=_D(d.charge_time_hours, "0.001"),
        charge_cost_usd=d.charge_cost_usd,
        total_hours=_D(d.total_hours, "0.001"),
        projected_arrival=d.projected_arrival,
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
) -> tuple[Assessment, FeasibilityResult]:
    result = evaluate(
        truck=truck, load=load, soc_start_pct=soc_start_pct, params=params,
        router=router, charging_providers=charging_providers,
    )
    row = persist_assessment(
        db, truck=truck, load=load, soc_start_pct=soc_start_pct, params=params, result=result
    )
    return row, result
