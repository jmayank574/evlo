"""Adapter tests: request construction, response parsing, and cache behavior.

HTTP is mocked with representative real API response *shapes* (not fabricated
product data) so parsing is verified offline. Live smoke tests run separately
once keys are provisioned.
"""

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.adapters.base import ProviderError
from app.adapters.charging import (
    CachedChargingProvider,
    NRELStations,
    OCMStations,
    haversine_mi,
)
from app.adapters.routing import CachedRouter, MapboxRouter
from app.db.base import Base
from app.models import ChargingStation, RouteCache

LA = (34.0522, -118.2437)
OAK = (37.8044, -122.2712)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Mapbox routing
# --------------------------------------------------------------------------- #


def test_mapbox_parses_distance_and_duration():
    def handler(req: httpx.Request) -> httpx.Response:
        assert "directions/v5/mapbox/driving" in str(req.url)
        assert req.url.params["access_token"] == "tok"
        # 643737.6 m = 400 mi; 28800 s = 8 h
        return httpx.Response(200, json={
            "routes": [{"distance": 643737.6, "duration": 28800,
                        "geometry": {"type": "LineString", "coordinates": []}}]
        })

    router = MapboxRouter("tok", client=_client(handler))
    route = router.route(LA, OAK)
    assert route.distance_mi == pytest.approx(400.0, abs=0.01)
    assert route.duration_hours == pytest.approx(8.0)
    assert route.provider == "mapbox"
    assert route.geometry["type"] == "LineString"


def test_mapbox_raises_without_token():
    with pytest.raises(ProviderError):
        MapboxRouter("", client=_client(lambda r: httpx.Response(200, json={}))).route(LA, OAK)


def test_mapbox_raises_on_no_route():
    router = MapboxRouter("tok", client=_client(lambda r: httpx.Response(200, json={"routes": []})))
    with pytest.raises(ProviderError):
        router.route(LA, OAK)


def test_mapbox_raises_loudly_on_http_error():
    router = MapboxRouter("tok", client=_client(lambda r: httpx.Response(422, json={"message": "bad"})))
    with pytest.raises(ProviderError):
        router.route(LA, OAK)


# --------------------------------------------------------------------------- #
# CachedRouter
# --------------------------------------------------------------------------- #


def test_cached_router_hits_cache_second_time(session):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"routes": [{"distance": 160934.4, "duration": 7200}]})

    inner = MapboxRouter("tok", client=_client(handler))
    cached = CachedRouter(inner, session, ttl_seconds=3600)

    r1 = cached.route(LA, OAK)
    r2 = cached.route(LA, OAK)
    assert calls["n"] == 1  # second call served from DB cache
    assert r1.distance_mi == pytest.approx(r2.distance_mi)
    assert session.scalar(select(func.count()).select_from(RouteCache)) == 1


# --------------------------------------------------------------------------- #
# NREL parsing
# --------------------------------------------------------------------------- #


def test_nrel_parses_stations_and_filters_missing_coords():
    def handler(req):
        assert req.url.params["fuel_type"] == "ELEC"
        assert req.url.params["ev_charging_level"] == "dc_fast"
        return httpx.Response(200, json={"fuel_stations": [
            {"id": 101, "station_name": "Electrify America - LA", "ev_network": "Electrify America",
             "latitude": 34.05, "longitude": -118.24, "ev_dc_fast_num": 6,
             "ev_connector_types": ["CCS", "CHADEMO"]},
            {"id": 102, "station_name": "Broken", "latitude": None, "longitude": None},
        ]})

    stations = NRELStations("key", client=_client(handler)).stations_near(34.05, -118.24, 50)
    assert len(stations) == 1  # the coord-less one is dropped
    s = stations[0]
    assert s.source == "NREL"
    assert s.external_id == "101"
    assert s.num_dc_fast_ports == 6
    assert s.max_power_kw is None  # NREL doesn't publish kW; we don't invent it
    assert "CCS" in s.connector_types


def test_nrel_raises_without_key():
    with pytest.raises(ProviderError):
        NRELStations("", client=_client(lambda r: httpx.Response(200, json={}))).stations_near(0, 0, 10)


# --------------------------------------------------------------------------- #
# OCM parsing (power detail)
# --------------------------------------------------------------------------- #


def test_ocm_parses_max_power_from_connections():
    def handler(req):
        return httpx.Response(200, json=[{
            "ID": 555,
            "AddressInfo": {"Title": "Tesla Megacharger", "Latitude": 35.37, "Longitude": -119.01},
            "OperatorInfo": {"Title": "Tesla"},
            "Connections": [
                {"PowerKW": 250, "ConnectionType": {"Title": "CCS"}, "Level": {"IsFastChargeCapable": True}},
                {"PowerKW": 1200, "ConnectionType": {"Title": "Tesla MCS"}, "Level": {"IsFastChargeCapable": True}},
            ],
        }])

    stations = OCMStations("key", client=_client(handler)).stations_near(35.37, -119.01, 25)
    assert len(stations) == 1
    s = stations[0]
    assert s.source == "OCM"
    assert s.max_power_kw == 1200  # max across connections
    assert s.num_dc_fast_ports == 2
    assert set(s.connector_types) == {"CCS", "Tesla MCS"}


# --------------------------------------------------------------------------- #
# CachedChargingProvider
# --------------------------------------------------------------------------- #


def test_charging_cache_upserts_and_serves_from_cache(session):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"fuel_stations": [
            {"id": 201, "station_name": "Stop A", "ev_network": "EVgo",
             "latitude": 35.00, "longitude": -119.00, "ev_dc_fast_num": 4,
             "ev_connector_types": ["CCS"]},
        ]})

    provider = CachedChargingProvider(NRELStations("key", client=_client(handler)), session, ttl_seconds=3600)
    first = provider.stations_near(35.0, -119.0, 25)
    second = provider.stations_near(35.0, -119.0, 25)

    assert calls["n"] == 1  # second served from cache
    assert len(first) == len(second) == 1
    # provenance persisted on the row
    row = session.scalar(select(ChargingStation))
    assert row.data_source == "NREL"
    assert row.fetched_at is not None
    assert row.expires_at is not None


def test_haversine_known_distance():
    # LA to Oakland is ~ 340 miles great-circle.
    d = haversine_mi(*LA, *OAK)
    assert 330 < d < 350
