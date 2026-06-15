"""Provider wiring. Real adapters are constructed per-request (so each gets the
request's DB session for caching). Tests override `get_providers` with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.adapters.base import ChargingProvider, RoutingProvider
from app.adapters.charging import CachedChargingProvider, NRELStations, OCMStations
from app.adapters.routing import CachedRouter, MapboxRouter
from app.core.config import get_settings
from app.db.session import get_db


@dataclass
class Providers:
    router: RoutingProvider
    charging: list[ChargingProvider]


def get_providers(db: Session) -> Providers:
    s = get_settings()
    router = CachedRouter(MapboxRouter(s.mapbox_token), db, s.route_cache_ttl_seconds)
    charging = [
        CachedChargingProvider(NRELStations(s.nrel_api_key), db, s.charger_cache_ttl_seconds),
        CachedChargingProvider(OCMStations(s.openchargemap_api_key), db, s.charger_cache_ttl_seconds),
    ]
    return Providers(router=router, charging=charging)


__all__ = ["Providers", "get_providers", "get_db"]
