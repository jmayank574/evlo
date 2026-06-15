"""Routing providers. Mapbox Directions is the default (real road distance +
drive time); a DB-backed cache wraps it so repeat lanes don't re-bill the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import Coord, ProviderError, Route, RoutingProvider
from app.models import RouteCache

_METERS_PER_MILE = 1609.344


class MapboxRouter:
    """Real road routing via the Mapbox Directions API (server-side token)."""

    name = "mapbox"

    def __init__(
        self,
        token: str,
        client: httpx.Client | None = None,
        base_url: str = "https://api.mapbox.com",
    ) -> None:
        self._token = token
        self._client = client or httpx.Client(timeout=25.0)
        self._base_url = base_url

    def route(self, origin: Coord, dest: Coord) -> Route:
        if not self._token:
            raise ProviderError("MAPBOX_TOKEN is not set; cannot route.")
        o_lat, o_lon = origin
        d_lat, d_lon = dest
        path = f"/directions/v5/mapbox/driving/{o_lon},{o_lat};{d_lon},{d_lat}"
        try:
            resp = self._client.get(
                f"{self._base_url}{path}",
                params={
                    "access_token": self._token,
                    "geometries": "geojson",
                    "overview": "simplified",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # fail loudly, do not fabricate
            raise ProviderError(f"Mapbox routing failed: {exc}") from exc

        data = resp.json()
        routes = data.get("routes") or []
        if not routes:
            raise ProviderError(f"Mapbox returned no route for {origin} -> {dest}.")
        r = routes[0]
        return Route(
            distance_mi=r["distance"] / _METERS_PER_MILE,
            duration_hours=r["duration"] / 3600.0,
            provider=self.name,
            geometry=r.get("geometry"),
        )


def _q(value: float) -> Decimal:
    """Quantize a coordinate to 4 decimals (~11 m) for a stable cache key."""
    return Decimal(str(round(value, 4)))


class CachedRouter:
    """Wraps any RoutingProvider with a DB cache keyed by rounded endpoints."""

    def __init__(self, inner: RoutingProvider, db: Session, ttl_seconds: int) -> None:
        self._inner = inner
        self._db = db
        self._ttl = ttl_seconds

    @property
    def name(self) -> str:
        return self._inner.name

    def route(self, origin: Coord, dest: Coord) -> Route:
        now = datetime.now(timezone.utc)
        o_lat, o_lon = _q(origin[0]), _q(origin[1])
        d_lat, d_lon = _q(dest[0]), _q(dest[1])

        hit = self._db.scalar(
            select(RouteCache).where(
                RouteCache.provider == self._inner.name,
                RouteCache.origin_lat == o_lat,
                RouteCache.origin_lon == o_lon,
                RouteCache.dest_lat == d_lat,
                RouteCache.dest_lon == d_lon,
                RouteCache.expires_at > now,
            )
        )
        if hit is not None:
            return Route(
                distance_mi=float(hit.distance_mi),
                duration_hours=float(hit.duration_hours),
                provider=hit.provider,
                geometry=hit.geometry,
            )

        route = self._inner.route(origin, dest)
        # Upsert (delete any stale row for this key first to respect the unique key).
        stale = self._db.scalar(
            select(RouteCache).where(
                RouteCache.provider == self._inner.name,
                RouteCache.origin_lat == o_lat,
                RouteCache.origin_lon == o_lon,
                RouteCache.dest_lat == d_lat,
                RouteCache.dest_lon == d_lon,
            )
        )
        if stale is not None:
            self._db.delete(stale)
            self._db.flush()
        self._db.add(
            RouteCache(
                provider=route.provider,
                origin_lat=o_lat, origin_lon=o_lon, dest_lat=d_lat, dest_lon=d_lon,
                distance_mi=Decimal(str(round(route.distance_mi, 2))),
                duration_hours=Decimal(str(round(route.duration_hours, 3))),
                geometry=route.geometry,
                expires_at=now + timedelta(seconds=self._ttl),
            )
        )
        self._db.commit()
        return route
