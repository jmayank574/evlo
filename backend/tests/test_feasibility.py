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
    assess_truck,
    build_load_context,
    cumulative_points,
    find_corridor_chargers,
    run_feasibility,
    run_fleet,
    subsample,
)
from app.adapters.charging import haversine_mi


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
        return [s for s in self._stations if haversine_mi(lat, lon, s.lat, s.lon) <= radius_mi]


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


def _truck(usable=Decimal("500"), **over):
    base = dict(
        make="Test", model="Rig", variant="v1",
        usable_kwh=usable, published_range_mi=250, gvwr_lb=82000,
        max_charge_kw=Decimal("350"), base_consumption_kwh_per_mi=Decimal("2.0"),
        reference_payload_lb=40000, spec_source_url="x",
        spec_accessed_date=datetime(2026, 6, 14).date(), provenance={},
    )
    base.update(over)
    return Truck(**base)


def _load(weight_lb=40000, **over):
    base = dict(
        reference="LD-T", origin_label="A", origin_lat=Decimal("35.0"), origin_lon=Decimal("-119.0"),
        dest_label="B", dest_lat=Decimal("35.0"), dest_lon=Decimal("-115.466"), weight_lb=weight_lb,
        pickup_window_start=datetime(2026, 6, 15, 6, tzinfo=timezone.utc),
        pickup_window_end=datetime(2026, 6, 15, 8, tzinfo=timezone.utc),
        delivery_window_start=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
        delivery_window_end=datetime(2026, 6, 16, 0, tzinfo=timezone.utc),
        data_source="synthetic",
    )
    base.update(over)
    return Load(**base)


# Fixed "now" so arrive-by feasibility isn't sensitive to the real wall clock.
NOW = datetime(2026, 6, 15, 6, 0, tzinfo=timezone.utc)


def _ctx(router, stations):
    return build_load_context(_load(), router, [FakeCharging(stations)])


# --- Pure helpers ---------------------------------------------------------- #

def test_cumulative_points_distances():
    alongs = [round(a) for _, a in cumulative_points(GEOMETRY)]
    assert alongs[0] == 0 and 65 <= alongs[1] <= 75 and 195 <= alongs[3] <= 205


def test_subsample_respects_min_gap():
    alongs = [round(a) for _, a in subsample(cumulative_points(GEOMETRY), 60.0)]
    assert alongs[0] == 0 and alongs[-1] >= 195


def test_find_corridor_dedupes_and_tags_min_along():
    s = StationResult("OCM", "A", 35.0, -117.763, max_power_kw=350)
    found = find_corridor_chargers([FakeCharging([s])], cumulative_points(GEOMETRY), 12.0)
    assert len(found) == 1 and 65 <= found[0].along_route_mi <= 75


# --- assess_truck against a shared context --------------------------------- #

def test_assess_truck_feasible_no_charging():
    ctx = _ctx(FakeRouter(100.0, 2.0, GEOMETRY), [])
    res = assess_truck(truck=_truck(), load=_load(), soc_start_pct=100.0, params=ModelParams(), ctx=ctx, now=NOW)
    assert res.domain.verdict == Verdict.FEASIBLE
    assert res.domain.num_charge_stops == 0


def test_assess_truck_feasible_with_charging_picks_reachable():
    c1 = StationResult("OCM", "C1", 35.0, -117.763, network="EVgo", name="Stop B", max_power_kw=350)
    c2 = StationResult("OCM", "C2", 35.0, -116.526, network="EA", name="Stop C", max_power_kw=1000)
    ctx = _ctx(FakeRouter(200.0, 4.0, GEOMETRY), [c1, c2])
    # soc 55 -> usable 200 -> reachable 100 mi: C1(~70) reachable, finishes in 1 stop.
    res = assess_truck(truck=_truck(), load=_load(), soc_start_pct=55.0, params=ModelParams(), ctx=ctx, now=NOW)
    assert res.domain.verdict == Verdict.FEASIBLE_WITH_CHARGING
    assert res.domain.num_charge_stops == 1
    assert res.domain.stops[0].ref == "OCM:C1"


def test_assess_truck_infeasible_when_no_charger():
    ctx = _ctx(FakeRouter(200.0, 4.0, GEOMETRY), [])  # no chargers
    res = assess_truck(truck=_truck(), load=_load(), soc_start_pct=55.0, params=ModelParams(), ctx=ctx, now=NOW)
    assert res.domain.verdict == Verdict.INFEASIBLE
    assert res.domain.num_charge_stops == 0


# --- persistence + fleet --------------------------------------------------- #

@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def test_run_feasibility_persists_reproducible_record(session):
    truck, load = _truck(), _load()
    session.add_all([truck, load])
    session.commit()
    c1 = StationResult("OCM", "C1", 35.0, -117.763, name="Stop B", max_power_kw=350)
    row, _ = run_feasibility(
        session, truck=truck, load=load, soc_start_pct=55.0, params=ModelParams(),
        router=FakeRouter(200.0, 4.0, GEOMETRY), charging_providers=[FakeCharging([c1])], now=NOW,
    )
    persisted = session.scalar(select(Assessment).where(Assessment.id == row.id))
    assert persisted.verdict == "feasible_with_charging"
    assert persisted.num_charge_stops == 1
    assert persisted.truck_id == truck.id
    assert persisted.load_snapshot["data_source"] == "synthetic"
    assert persisted.reserve_pct == Decimal("15.00")
    # chargers_used stores only the picked stop(s), with order + plan detail
    assert len(persisted.chargers_used) == 1
    stop = persisted.chargers_used[0]
    assert stop["picked"] is True and stop["order"] == 1 and stop["external_id"] == "C1"
    assert stop["energy_added_kwh"] > 0 and stop["charge_minutes"] > 0


def test_run_fleet_assesses_every_truck_once(session):
    big = _truck(usable=Decimal("900"), variant="big")   # can do it without charging
    small = _truck(usable=Decimal("300"), variant="small")  # will need charging
    load = _load()
    session.add_all([big, small, load])
    session.commit()
    c1 = StationResult("OCM", "C1", 35.0, -117.763, name="Stop B", max_power_kw=350)
    rows = run_fleet(
        session, load=load, trucks=[big, small], soc_start_pct=80.0, params=ModelParams(),
        router=FakeRouter(200.0, 4.0, GEOMETRY), charging_providers=[FakeCharging([c1])], now=NOW,
    )
    assert len(rows) == 2
    by_truck = {r.truck_id: r for r in rows}
    assert by_truck[big.id].verdict == "feasible"
    assert by_truck[small.id].charging_required is True
