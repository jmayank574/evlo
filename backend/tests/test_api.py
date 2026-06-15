"""API integration tests. DB + providers are overridden with a test SQLite and
fake adapters so the endpoints are exercised end-to-end without network."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.base import Route, StationResult
from app.api.deps import Providers, get_db
from app.api.routes import provider_dep
from app.db.base import Base
from app.main import app
from app.models import Load, Truck


class FakeRouter:
    name = "fake"

    def __init__(self, distance_mi=100.0, duration_hours=2.0):
        self._r = Route(distance_mi, duration_hours, "fake",
                        {"type": "LineString", "coordinates": [[-119, 35], [-118, 35]]})

    def route(self, origin, dest):
        return self._r


class FakeCharging:
    source = "OCM"

    def __init__(self, stations=()):
        self._stations = list(stations)

    def stations_near(self, lat, lon, radius_mi):
        return self._stations


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as s:
        truck = Truck(
            make="Test", model="Rig", variant="v1", usable_kwh=Decimal("500"),
            published_range_mi=250, gvwr_lb=82000, max_charge_kw=Decimal("350"),
            base_consumption_kwh_per_mi=Decimal("2.0"), reference_payload_lb=40000,
            spec_source_url="x", spec_accessed_date=datetime(2026, 6, 14).date(),
            provenance={"usable_kwh": {"trust": "manufacturer"}},
        )
        load = Load(
            reference="LD-T", origin_label="A", origin_lat=Decimal("35.0"), origin_lon=Decimal("-119.0"),
            dest_label="B", dest_lat=Decimal("35.0"), dest_lon=Decimal("-118.0"), weight_lb=40000,
            # Far-future deadline so arrive-by feasibility never depends on the
            # real wall clock (these are absolute, not relative, sample dates).
            pickup_window_start=datetime(2026, 6, 15, 6, tzinfo=timezone.utc),
            pickup_window_end=datetime(2026, 6, 15, 8, tzinfo=timezone.utc),
            delivery_window_start=datetime(2099, 1, 1, 0, tzinfo=timezone.utc),
            delivery_window_end=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
            data_source="synthetic",
        )
        s.add_all([truck, load])
        s.commit()
        ids = {"truck": truck.id, "load": load.id}

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    def override_providers():
        return Providers(router=FakeRouter(), charging=[FakeCharging()])

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[provider_dep] = override_providers
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def test_health(client):
    c, _ = client
    assert c.get("/api/health").json() == {"status": "ok"}


def test_list_trucks_exposes_provenance(client):
    c, _ = client
    r = c.get("/api/trucks")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["provenance"]["usable_kwh"]["trust"] == "manufacturer"


def test_list_loads_badges_synthetic(client):
    c, _ = client
    body = c.get("/api/loads").json()
    assert body[0]["data_source"] == "synthetic"


def test_methodology_flags_estimate(client):
    c, _ = client
    body = c.get("/api/methodology").json()
    pc = next(p for p in body["params"] if p["name"] == "payload_coefficient_kwh_per_mi_per_ton")
    assert pc["is_estimate"] is True
    assert any("nlr.gov" in s["url"] for s in body["sources"])


def test_assess_feasible(client):
    c, ids = client
    r = c.post("/api/assess", json={
        "truck_id": str(ids["truck"]), "load_id": str(ids["load"]), "soc_start_pct": 100.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "feasible"
    assert body["charging_required"] is False
    assert body["load_snapshot"]["data_source"] == "synthetic"


def test_assess_param_override_changes_verdict(client):
    """The knob the user can turn: a 90% reserve makes the same trip need charging,
    and with no chargers available the verdict flips to infeasible."""
    c, ids = client
    r = c.post("/api/assess", json={
        "truck_id": str(ids["truck"]), "load_id": str(ids["load"]), "soc_start_pct": 100.0,
        "params": {"reserve_pct": 90.0},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["reserve_pct"] == 90.0
    assert body["verdict"] == "infeasible"


def test_assess_404_for_unknown_truck(client):
    c, ids = client
    r = c.post("/api/assess", json={
        "truck_id": "00000000-0000-0000-0000-000000000000",
        "load_id": str(ids["load"]), "soc_start_pct": 50.0,
    })
    assert r.status_code == 404


def test_assess_fleet_returns_ranked_items(client):
    c, ids = client
    r = c.post("/api/assess/fleet", json={"load_id": str(ids["load"]), "soc_start_pct": 100.0})
    assert r.status_code == 200
    body = r.json()
    assert body["load_id"] == str(ids["load"])
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert "verdict" in item and "num_charge_stops" in item
    assert item["arrival_margin_min"] is not None


def test_get_assessment_roundtrip(client):
    c, ids = client
    created = c.post("/api/assess", json={
        "truck_id": str(ids["truck"]), "load_id": str(ids["load"]), "soc_start_pct": 100.0,
    }).json()
    fetched = c.get(f"/api/assessments/{created['id']}").json()
    assert fetched["id"] == created["id"]
    assert fetched["verdict"] == created["verdict"]
