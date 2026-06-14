"""Feasibility orchestration tests with injected fake providers (no network)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.adapters.base import Route, StationResult
from app.db.base import Base
from app.domain.energy import ModelParams, Verdict
from app.models import Assessment, Load, Truck
from app.services.feasibility import (
    CorridorCharger,
    cumulative_points,
    evaluate,
    find_corridor_chargers,
    pick_charge_stop,
    run_feasibility,
    subsample,
)
from app.services import feasibility
from app.adapters.charging import haversine_mi


# --- Fakes ----------------------------------------------------------------- #

class FakeRouter:
    name = "fake"

    def __init__(self, distance_mi, duration_hours, geometry):
        self._r = Route(distance_mi, duration_hours, "fake", geometry)

    def route(self, origin, dest):
        return self._r


class FakeCharging:
    """Returns the stations that fall within `radius` of the queried point."""

    source = "OCM"

    def __init__(self, stations):
        self._stations = stations

    def stations_near(self, lat, lon, radius_mi):
        return [
            s for s in self._stations
            if haversine_mi(lat, lon, s.lat, s.lon) <= radius_mi
        ]


# Straight east-west line at lat 35: ~56.6 mi per degree of longitude.
GEOMETRY = {
    "type": "LineString",
    "coordinates": [
        [-119.000, 35.0],  # along 0
        [-117.763, 35.0],  # along ~70
        [-116.526, 35.0],  # along ~140
        [-115.466, 35.0],  # along ~200
    ],
}


def _truck(**over):
    base = dict(
        make="Test", model="Rig", variant="v1",
        usable_kwh=Decimal("500"), published_range_mi=250, gvwr_lb=82000,
        max_charge_kw=Decimal("350"), base_consumption_kwh_per_mi=Decimal("2.0"),
        reference_payload_lb=40000, spec_source_url="x",
        spec_accessed_date=datetime(2026, 6, 14).date(), provenance={},
    )
    base.update(over)
    return Truck(**base)


def _load(weight_lb=40000, **over):
    base = dict(
        reference="LD-T", origin_label="A", origin_lat=Decimal("35.0"), origin_lon=Decimal("-119.0"),
        dest_label="B", dest_lat=Decimal("35.0"), dest_lon=Decimal("-115.466"),
        weight_lb=weight_lb,
        pickup_window_start=datetime(2026, 6, 15, 6, tzinfo=timezone.utc),
        pickup_window_end=datetime(2026, 6, 15, 8, tzinfo=timezone.utc),
        delivery_window_start=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
        delivery_window_end=datetime(2026, 6, 16, 0, tzinfo=timezone.utc),
        data_source="synthetic",
    )
    base.update(over)
    return Load(**base)


# --- Pure helper tests ----------------------------------------------------- #

def test_cumulative_points_distances():
    pts = cumulative_points(GEOMETRY)
    alongs = [round(a) for _, a in pts]
    assert alongs[0] == 0
    assert 65 <= alongs[1] <= 75
    assert 135 <= alongs[2] <= 145
    assert 195 <= alongs[3] <= 205


def test_subsample_respects_min_gap():
    pts = cumulative_points(GEOMETRY)
    sub = subsample(pts, 60.0)
    alongs = [round(a) for _, a in sub]
    # 0, ~70, ~140, ~200 all kept (gaps ~70 >= 60)
    assert alongs[0] == 0 and alongs[-1] >= 195


def test_find_corridor_dedupes_and_tags_min_along():
    s = StationResult("OCM", "A", 35.0, -117.763, max_power_kw=350)
    provider = FakeCharging([s])
    pts = cumulative_points(GEOMETRY)
    found = find_corridor_chargers([provider], pts, 12.0)
    assert len(found) == 1
    assert 65 <= found[0].along_route_mi <= 75


def test_pick_charge_stop_prefers_reachable_high_power():
    near = CorridorCharger(StationResult("OCM", "near", 35, -117.7, max_power_kw=350), 70)
    far = CorridorCharger(StationResult("OCM", "far", 35, -116.5, max_power_kw=1000), 140)
    unknown = CorridorCharger(StationResult("OCM", "unk", 35, -117.7, max_power_kw=None), 70)
    pick = pick_charge_stop([near, far, unknown], reachable_distance_mi=100)
    assert pick.station.external_id == "near"  # far is higher power but unreachable


def test_pick_charge_stop_none_when_nothing_reachable():
    far = CorridorCharger(StationResult("OCM", "far", 35, -116.5, max_power_kw=1000), 140)
    assert pick_charge_stop([far], reachable_distance_mi=50) is None


# --- evaluate() ------------------------------------------------------------ #

def test_evaluate_feasible_no_charging():
    router = FakeRouter(100.0, 2.0, GEOMETRY)  # energy 200 < usable 425 at soc 100
    res = evaluate(
        truck=_truck(), load=_load(), soc_start_pct=100.0,
        params=ModelParams(), router=router, charging_providers=[FakeCharging([])],
    )
    assert res.domain.verdict == Verdict.FEASIBLE
    assert res.charge_stop is None
    assert res.corridor == []


def test_evaluate_feasible_with_charging_picks_reachable():
    c1 = StationResult("OCM", "C1", 35.0, -117.763, network="EVgo", name="Stop B", max_power_kw=350)
    c2 = StationResult("OCM", "C2", 35.0, -116.526, network="EA", name="Stop C", max_power_kw=1000)
    c3 = StationResult("OCM", "C3", 35.0, -117.763, name="No power", max_power_kw=None)
    router = FakeRouter(200.0, 4.0, GEOMETRY)
    # soc 55 -> usable 200 -> reachable_distance 100 mi: C1(~70) ok, C2(~140) not.
    res = evaluate(
        truck=_truck(), load=_load(), soc_start_pct=55.0,
        params=ModelParams(), router=router,
        charging_providers=[FakeCharging([c1, c2, c3])],
    )
    assert res.domain.verdict == Verdict.FEASIBLE_WITH_CHARGING
    assert res.charge_stop.station.external_id == "C1"
    assert res.domain.energy_to_add_kwh == pytest.approx(200.0, abs=1.0)


def test_evaluate_infeasible_when_no_reachable_charger():
    c2 = StationResult("OCM", "C2", 35.0, -116.526, max_power_kw=1000)  # only the far one
    router = FakeRouter(200.0, 4.0, GEOMETRY)
    res = evaluate(
        truck=_truck(), load=_load(), soc_start_pct=55.0,
        params=ModelParams(), router=router, charging_providers=[FakeCharging([c2])],
    )
    assert res.domain.verdict == Verdict.INFEASIBLE
    assert res.charge_stop is None


# --- persistence ----------------------------------------------------------- #

@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_run_feasibility_persists_reproducible_record(session):
    truck = _truck()
    load = _load()
    session.add_all([truck, load])
    session.commit()

    c1 = StationResult("OCM", "C1", 35.0, -117.763, name="Stop B", max_power_kw=350)
    router = FakeRouter(200.0, 4.0, GEOMETRY)
    row, result = run_feasibility(
        session, truck=truck, load=load, soc_start_pct=55.0, params=ModelParams(),
        router=router, charging_providers=[FakeCharging([c1])],
    )

    persisted = session.scalar(select(Assessment).where(Assessment.id == row.id))
    assert persisted.verdict == "feasible_with_charging"
    assert persisted.truck_id == truck.id
    assert persisted.load_snapshot["data_source"] == "synthetic"
    assert persisted.truck_snapshot["make"] == "Test"
    # ModelParams captured as typed columns
    assert persisted.reserve_pct == Decimal("15.00")
    assert persisted.charge_cost_usd > 0
    # chargers_used snapshot marks the picked stop
    picked = [c for c in persisted.chargers_used if c["picked"]]
    assert len(picked) == 1 and picked[0]["external_id"] == "C1"
