"""Provider-agnostic interfaces for routing and charging data.

Everything external sits behind these so providers are swappable (Mapbox ->
Valhalla, NREL <-> OCM) and so the energy model never depends on a vendor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# (latitude, longitude)
Coord = tuple[float, float]


class ProviderError(RuntimeError):
    """Raised when an external provider fails. We fail loudly rather than
    fabricate a value (core project rule: never invent a number to fill a gap).
    """


@dataclass(frozen=True)
class Route:
    distance_mi: float
    duration_hours: float
    provider: str
    geometry: dict | None = None  # GeoJSON LineString, for the map


@dataclass(frozen=True)
class StationResult:
    source: str  # "NREL" | "OCM"
    external_id: str
    lat: float
    lon: float
    network: str | None = None
    name: str | None = None
    num_dc_fast_ports: int | None = None
    max_power_kw: float | None = None
    connector_types: list[str] = field(default_factory=list)


@runtime_checkable
class RoutingProvider(Protocol):
    name: str

    def route(self, origin: Coord, dest: Coord) -> Route: ...


@runtime_checkable
class ChargingProvider(Protocol):
    source: str

    def stations_near(self, lat: float, lon: float, radius_mi: float) -> list[StationResult]: ...
