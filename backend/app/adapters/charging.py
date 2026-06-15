"""Charging-station providers: NREL AFDC (primary) and Open Charge Map
(supplement / power detail). A DB-backed cache upserts results into the
``charging_stations`` table so every cached charger keeps its source +
fetched_at provenance and expires (never treated as permanent truth).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import ChargingProvider, ProviderError, StationResult
from app.models import ChargingStation

_EARTH_RADIUS_MI = 3958.8


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_MI * math.asin(math.sqrt(a))


class NRELStations:
    """NREL/NLR Alternative Fuels Data Center — electric, DC-fast stations.

    Source note: NREL rebranded to the National Laboratory of the Rockies (NLR)
    and retired the nrel.gov domain on 2026-05-29. The base URL is now
    developer.nlr.gov; existing API keys are unchanged. See DATA_SOURCES.md.
    The stored ``source`` label remains "NREL" for data continuity (the dataset
    — the AFDC — is the same); the rename is documented rather than churned
    through the schema enum and historical rows.
    """

    source = "NREL"

    def __init__(self, api_key: str, client: httpx.Client | None = None,
                 base_url: str = "https://developer.nlr.gov") -> None:
        self._key = api_key
        self._client = client or httpx.Client(timeout=25.0)
        self._base_url = base_url

    def stations_near(self, lat: float, lon: float, radius_mi: float) -> list[StationResult]:
        if not self._key:
            raise ProviderError("NREL_API_KEY is not set; cannot fetch stations.")
        try:
            resp = self._client.get(
                f"{self._base_url}/api/alt-fuel-stations/v1/nearest.json",
                params={
                    "api_key": self._key,
                    "latitude": lat,
                    "longitude": lon,
                    "radius": radius_mi,
                    "fuel_type": "ELEC",
                    "ev_charging_level": "dc_fast",
                    "limit": 50,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"NREL request failed: {exc}") from exc

        out: list[StationResult] = []
        for s in resp.json().get("fuel_stations", []):
            if s.get("latitude") is None or s.get("longitude") is None:
                continue
            out.append(
                StationResult(
                    source=self.source,
                    external_id=str(s["id"]),
                    lat=float(s["latitude"]),
                    lon=float(s["longitude"]),
                    network=s.get("ev_network"),
                    name=s.get("station_name"),
                    num_dc_fast_ports=s.get("ev_dc_fast_num"),
                    # NREL does not reliably publish kW; leave None rather than guess.
                    max_power_kw=None,
                    connector_types=s.get("ev_connector_types") or [],
                )
            )
        return out


class OCMStations:
    """Open Charge Map — adds connector power (kW) detail NREL lacks."""

    source = "OCM"

    def __init__(self, api_key: str, client: httpx.Client | None = None,
                 base_url: str = "https://api.openchargemap.io") -> None:
        self._key = api_key
        self._client = client or httpx.Client(timeout=25.0)
        self._base_url = base_url

    def stations_near(self, lat: float, lon: float, radius_mi: float) -> list[StationResult]:
        if not self._key:
            raise ProviderError("OPENCHARGEMAP_API_KEY is not set; cannot fetch stations.")
        try:
            resp = self._client.get(
                f"{self._base_url}/v3/poi/",
                params={
                    "key": self._key,
                    "latitude": lat,
                    "longitude": lon,
                    "distance": radius_mi,
                    "distanceunit": "Miles",
                    "maxresults": 50,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Open Charge Map request failed: {exc}") from exc

        out: list[StationResult] = []
        for poi in resp.json():
            info = poi.get("AddressInfo") or {}
            if info.get("Latitude") is None or info.get("Longitude") is None:
                continue
            conns = poi.get("Connections") or []
            powers = [c.get("PowerKW") for c in conns if c.get("PowerKW")]
            connectors = [
                (c.get("ConnectionType") or {}).get("Title")
                for c in conns
                if (c.get("ConnectionType") or {}).get("Title")
            ]
            fast_ports = sum(
                1 for c in conns if (c.get("Level") or {}).get("IsFastChargeCapable")
            )
            out.append(
                StationResult(
                    source=self.source,
                    external_id=str(poi["ID"]),
                    lat=float(info["Latitude"]),
                    lon=float(info["Longitude"]),
                    network=(poi.get("OperatorInfo") or {}).get("Title"),
                    name=info.get("Title"),
                    num_dc_fast_ports=fast_ports or None,
                    max_power_kw=max(powers) if powers else None,
                    connector_types=connectors,
                )
            )
        return out


class CachedChargingProvider:
    """Caches station rows in the DB. If fresh rows exist within the radius for
    this source, serve them (respecting API quota); otherwise fetch live and
    upsert. ``expires_at`` bounds staleness; ``fetched_at`` records provenance.
    """

    def __init__(self, inner: ChargingProvider, db: Session, ttl_seconds: int) -> None:
        self._inner = inner
        self._db = db
        self._ttl = ttl_seconds

    @property
    def source(self) -> str:
        return self._inner.source

    def _fresh_rows_within(self, lat: float, lon: float, radius_mi: float):
        now = datetime.now(timezone.utc)
        rows = self._db.scalars(
            select(ChargingStation).where(
                ChargingStation.data_source == self._inner.source,
                ChargingStation.expires_at > now,
            )
        ).all()
        return [r for r in rows if haversine_mi(lat, lon, float(r.lat), float(r.lon)) <= radius_mi]

    def stations_near(self, lat: float, lon: float, radius_mi: float) -> list[StationResult]:
        cached = self._fresh_rows_within(lat, lon, radius_mi)
        if cached:
            return [self._row_to_result(r) for r in cached]

        results = self._inner.stations_near(lat, lon, radius_mi)
        self._upsert(results)
        return results

    def _upsert(self, results: list[StationResult]) -> None:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self._ttl)
        for r in results:
            existing = self._db.scalar(
                select(ChargingStation).where(
                    ChargingStation.data_source == r.source,
                    ChargingStation.external_id == r.external_id,
                )
            )
            values = dict(
                network=r.network,
                name=r.name,
                lat=Decimal(str(r.lat)),
                lon=Decimal(str(r.lon)),
                num_dc_fast_ports=r.num_dc_fast_ports,
                max_power_kw=Decimal(str(r.max_power_kw)) if r.max_power_kw is not None else None,
                connector_types=r.connector_types,
                fetched_at=now,
                expires_at=expires,
            )
            if existing is None:
                self._db.add(
                    ChargingStation(data_source=r.source, external_id=r.external_id, **values)
                )
            else:
                for k, v in values.items():
                    setattr(existing, k, v)
        self._db.commit()

    @staticmethod
    def _row_to_result(r: ChargingStation) -> StationResult:
        return StationResult(
            source=r.data_source,
            external_id=r.external_id,
            lat=float(r.lat),
            lon=float(r.lon),
            network=r.network,
            name=r.name,
            num_dc_fast_ports=r.num_dc_fast_ports,
            max_power_kw=float(r.max_power_kw) if r.max_power_kw is not None else None,
            connector_types=r.connector_types or [],
        )
