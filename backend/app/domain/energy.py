"""Voltpath energy & feasibility model.

This module is intentionally PURE: no I/O, no database, no network, no framework.
It is the auditable heart of the product. Every assumption is a named, visible
parameter on :class:`ModelParams` so a skeptical reviewer can see there is no
black box.

Grounding (see DATA_SOURCES.md):
  * Base consumption per truck is calibrated to a published/measured kWh/mi.
  * Payload sensitivity (`payload_coefficient_kwh_per_mi_per_ton`) is grounded in
    rolling-resistance physics and the ~51 Wh/ton-mile figure from
    arXiv:1804.05974, bracketed by NACFE measured 1.55-1.72 kWh/mi (Tesla Semi).

Units: energy in kWh, distance in miles, weight in pounds, power in kW, time in
hours. Money is the only quantity carried as Decimal (see `charge_cost_usd`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

LB_PER_TON: float = 2000.0


class Verdict(str, Enum):
    """The three-state operational answer a dispatcher needs."""

    FEASIBLE = "feasible"  # completes on time, no charging needed
    FEASIBLE_WITH_CHARGING = "feasible_with_charging"  # on time, but must charge
    INFEASIBLE = "infeasible"  # out of range with no viable charger, or can't make the window


@dataclass(frozen=True)
class TruckSpec:
    """Real, cited spec for a candidate truck. See DATA_SOURCES.md."""

    name: str
    usable_kwh: float
    base_consumption_kwh_per_mi: float  # measured/published, at `reference_payload_lb`
    reference_payload_lb: float
    max_charge_kw: float
    gvwr_lb: float


@dataclass(frozen=True)
class ModelParams:
    """Every assumption in the model, as a visible, tunable parameter.

    Defaults are deliberately conservative and are surfaced in the UI's
    Methodology panel. None of these is a hidden magic number.
    """

    # Arrive with at least this much battery left (safety reserve).
    reserve_pct: float = 15.0
    # Fixed handling/dwell time added to every trip (loading, gate, inspection).
    dwell_buffer_min: float = 30.0
    # Marginal kWh/mi added per ton of payload above the truck's reference payload.
    # Grounded in rolling-resistance physics (~51 Wh/ton-mile, arXiv:1804.05974).
    payload_coefficient_kwh_per_mi_per_ton: float = 0.025
    # Fraction of grid energy that lands in the battery (charging losses) -> cost only.
    charge_efficiency: float = 0.92
    # We only model charging up to this SoC cap. The high-SoC taper above this is
    # NOT modeled (flagged in the UI). A single stop cannot exceed this cap.
    charge_soc_cap_pct: float = 80.0
    # Minimum charger power (kW) usable for a Class 8 truck en route. Below this,
    # a stop is operationally unrealistic for freight (e.g. a 3 kW AC plug =
    # 50+ hours). Filters the corridor to genuine DC-fast charging; tunable.
    min_charger_power_kw: float = 150.0
    # Energy price for cost estimates.
    energy_price_per_kwh_usd: Decimal = Decimal("0.20")


# --------------------------------------------------------------------------- #
# Core physics (pure functions, each independently testable)
# --------------------------------------------------------------------------- #


def payload_tons(payload_lb: float) -> float:
    return payload_lb / LB_PER_TON


def consumption_kwh_per_mi(
    truck: TruckSpec, payload_lb: float, params: ModelParams
) -> float:
    """Payload-adjusted energy consumption.

    C(p) = C_base + k * (p - p_ref), with p in tons. Clamped to >= 0.
    """
    delta_tons = (payload_lb - truck.reference_payload_lb) / LB_PER_TON
    consumption = (
        truck.base_consumption_kwh_per_mi
        + params.payload_coefficient_kwh_per_mi_per_ton * delta_tons
    )
    return max(consumption, 0.0)


def energy_required_kwh(
    truck: TruckSpec, payload_lb: float, distance_mi: float, params: ModelParams
) -> float:
    """Energy to drive `distance_mi` carrying `payload_lb`."""
    return consumption_kwh_per_mi(truck, payload_lb, params) * distance_mi


def usable_energy_for_trip_kwh(
    truck: TruckSpec, soc_start_pct: float, params: ModelParams
) -> float:
    """Energy available to spend while still arriving with the reserve intact.

    = usable_kwh * (SOC_start - reserve_pct) / 100, clamped to >= 0.
    """
    frac = (soc_start_pct - params.reserve_pct) / 100.0
    return max(truck.usable_kwh * frac, 0.0)


def effective_charge_power_kw(truck: TruckSpec, station_max_kw: float) -> float:
    """Power actually delivered into the battery: limited by the weaker side."""
    return min(truck.max_charge_kw, station_max_kw)


def charge_time_hours(energy_to_add_kwh: float, effective_power_kw: float) -> float:
    if energy_to_add_kwh <= 0:
        return 0.0
    if effective_power_kw <= 0:
        raise ValueError("effective charge power must be positive")
    return energy_to_add_kwh / effective_power_kw


def charge_cost_usd(energy_to_add_kwh: float, params: ModelParams) -> Decimal:
    """Cost of the charge, in USD, accounting for charging losses (grid draw).

    Money is Decimal end-to-end here; the float->Decimal boundary lives in this
    one function (see DECISIONS.md D4).
    """
    if energy_to_add_kwh <= 0:
        return Decimal("0.00")
    grid_energy = Decimal(str(energy_to_add_kwh)) / Decimal(str(params.charge_efficiency))
    return (grid_energy * params.energy_price_per_kwh_usd).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Multi-stop charge planning
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChargeOption:
    """A usable corridor charger the planner may stop at. ``ref`` maps back to
    the real station in the integration layer; the model needs only its position
    along the route and its power."""

    ref: str
    along_mi: float
    power_kw: float


@dataclass(frozen=True)
class ChargeStop:
    ref: str
    along_mi: float
    power_kw: float
    energy_added_kwh: float
    charge_hours: float


@dataclass(frozen=True)
class ChargePlan:
    can_reach: bool  # could the truck reach the destination (possibly via stops)?
    stops: tuple[ChargeStop, ...]
    stranded_at_mi: float | None  # farthest reachable point if it cannot make it


_GREEDY_MAX_STOPS = 50


def plan_charging(
    *,
    usable_kwh: float,
    start_soc_pct: float,
    consumption_kwh_per_mi: float,
    total_distance_mi: float,
    truck_max_charge_kw: float,
    corridor: list[ChargeOption],
    params: ModelParams,
) -> ChargePlan:
    """Greedy range-anxiety planner.

    Drive as far as the current charge allows (down to the reserve floor). When
    the destination is out of reach, stop at the **farthest reachable** corridor
    charger (minimizing the number of stops) and add **just enough to finish** —
    or, if a single fill can't get there, top up to the SoC cap and continue.
    Returns the ordered stops, or flags where the truck would strand if a charger
    gap exceeds its range.

    Stated simplifications: constant power up to the SoC cap (no high-SoC taper);
    only chargers with a known power (kW) are usable, since NREL/NLR AFDC does not
    publish kW — we never invent one.
    """
    if consumption_kwh_per_mi <= 0:
        return ChargePlan(True, (), None)

    reserve_floor = usable_kwh * params.reserve_pct / 100.0
    cap_kwh = usable_kwh * params.charge_soc_cap_pct / 100.0
    energy = usable_kwh * start_soc_pct / 100.0
    pos = 0.0
    opts = sorted(
        (
            c for c in corridor
            if c.power_kw and c.power_kw >= params.min_charger_power_kw
            and c.along_mi <= total_distance_mi
        ),
        key=lambda c: c.along_mi,
    )
    stops: list[ChargeStop] = []

    for _ in range(_GREEDY_MAX_STOPS + 1):
        range_now = (energy - reserve_floor) / consumption_kwh_per_mi
        if pos + range_now >= total_distance_mi - 1e-6:
            return ChargePlan(True, tuple(stops), None)

        reachable = [c for c in opts if pos + 1e-6 < c.along_mi <= pos + range_now + 1e-6]
        if not reachable:
            return ChargePlan(False, tuple(stops), pos + range_now)

        choice = max(reachable, key=lambda c: c.along_mi)
        energy -= consumption_kwh_per_mi * (choice.along_mi - pos)
        pos = choice.along_mi

        need_to_finish = consumption_kwh_per_mi * (total_distance_mi - pos) + reserve_floor - energy
        headroom = cap_kwh - energy
        add = min(max(need_to_finish, 0.0), headroom)
        if add <= 1e-6:
            # Already at/above the cap and still can't reach the next point.
            return ChargePlan(False, tuple(stops), pos + range_now)

        eff_power = min(truck_max_charge_kw, choice.power_kw)
        stops.append(ChargeStop(choice.ref, choice.along_mi, choice.power_kw, add, add / eff_power))
        energy += add

    return ChargePlan(False, tuple(stops), pos)


# --------------------------------------------------------------------------- #
# Verdict assembly
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Assessment:
    """The auditable result: a verdict plus every number behind it."""

    verdict: Verdict
    reasons: tuple[str, ...]
    consumption_kwh_per_mi: float
    energy_required_kwh: float
    usable_energy_for_trip_kwh: float
    charging_required: bool
    num_charge_stops: int
    stops: tuple[ChargeStop, ...]
    # Where the truck would run out (mile along route) if it cannot reach the
    # destination even with charging; None when it reaches. Drives the range bar.
    stranded_at_mi: float | None
    energy_to_add_kwh: float
    charge_time_hours: float
    charge_cost_usd: Decimal
    drive_hours: float
    dwell_hours: float
    total_hours: float
    # Time facts. `latest_departure` = deadline - trip_duration (the latest a
    # truck can roll and still make it). `projected_arrival` = depart_at +
    # trip_duration (meaningful in depart-at mode; equals the deadline in
    # arrive-by). Both derive from the SAME trip_duration, so charge time feeds
    # the backwards math automatically.
    time_mode: str
    latest_departure: datetime
    projected_arrival: datetime
    now_reference: datetime | None
    on_time: bool


# Time-of-day modes for the assessment.
DEPART_AT = "depart_at"
ARRIVE_BY = "arrive_by"


def _clock(dt: datetime) -> str:
    """'Jun 15, 1:59 AM' — robust on Windows (no %-I)."""
    h = dt.hour % 12 or 12
    return f"{dt:%b} {dt.day}, {h}:{dt:%M} {dt:%p}"


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes (e.g. from SQLite) as UTC, so time math is safe
    regardless of where the datetime came from."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mins(delta) -> float:
    return delta.total_seconds() / 60.0


def assess(
    *,
    truck: TruckSpec,
    payload_lb: float,
    distance_mi: float,
    drive_hours: float,
    deliver_by: datetime,
    soc_start_pct: float,
    params: ModelParams,
    corridor: list[ChargeOption] | None = None,
    time_mode: str = DEPART_AT,
    depart_at: datetime | None = None,
    pickup_window_start: datetime | None = None,
    now: datetime | None = None,
) -> Assessment:
    """Produce the time-aware three-state verdict.

    `time_mode`:
      * DEPART_AT  — `depart_at` is given; arrival = depart_at + trip_duration;
        time-feasible iff arrival <= deliver_by.
      * ARRIVE_BY  — the deadline is given; latest_departure = deliver_by -
        trip_duration; time-feasible iff latest_departure >= max(now, pickup_open).

    Range and time are independent gates: a truck that reaches the destination
    can still be time-infeasible (and vice-versa). Stays pure / unit-testable.
    """
    corridor = corridor or []
    consumption = consumption_kwh_per_mi(truck, payload_lb, params)
    energy_req = consumption * distance_mi
    usable = usable_energy_for_trip_kwh(truck, soc_start_pct, params)
    dwell_hours = params.dwell_buffer_min / 60.0

    plan = plan_charging(
        usable_kwh=truck.usable_kwh,
        start_soc_pct=soc_start_pct,
        consumption_kwh_per_mi=consumption,
        total_distance_mi=distance_mi,
        truck_max_charge_kw=truck.max_charge_kw,
        corridor=corridor,
        params=params,
    )
    stops = plan.stops
    num_stops = len(stops)
    stranded_at_mi = None if plan.can_reach else plan.stranded_at_mi
    energy_to_add = sum(s.energy_added_kwh for s in stops)
    charge_hours = sum(s.charge_hours for s in stops)
    cost = charge_cost_usd(energy_to_add, params)
    total_hours = drive_hours + charge_hours + dwell_hours
    trip = timedelta(hours=total_hours)

    # --- Time facts (mode-aware), both off the same trip_duration ---
    latest_departure = _aware(deliver_by) - trip
    now_reference: datetime | None = None
    time_ok = True
    time_reason = ""

    # NOTE: reasons use tz-independent DURATIONS, never absolute clock times.
    # Absolute times (roll-by / arrival / deadline) are rendered once, by the
    # frontend, in the viewer's local timezone — see DECISIONS D17.
    if time_mode == ARRIVE_BY:
        now_reference = _aware(now) if now else datetime.now(timezone.utc)
        pw = _aware(pickup_window_start) if pickup_window_start else None
        earliest_roll = max(now_reference, pw) if pw else now_reference
        projected_arrival = deliver_by  # rolling at the latest -> arrive exactly on the deadline
        time_ok = latest_departure >= earliest_roll
        if time_ok:
            slack = _mins(latest_departure - earliest_roll)
            time_reason = (
                f"On time: the latest safe departure leaves {slack:.0f} min of slack "
                f"before the deadline."
            )
        elif pw is not None and pw > now_reference:
            short = _mins(pw - latest_departure)
            time_reason = (
                f"Infeasible on time: would need to roll {short:.0f} min before the load "
                f"opens for pickup."
            )
        else:
            late = _mins(now_reference - latest_departure)
            time_reason = (
                f"Infeasible on time: the latest safe departure was {late:.0f} min ago."
            )
    else:  # DEPART_AT
        if depart_at is None:
            raise ValueError("depart_at is required in depart-at mode")
        projected_arrival = depart_at + trip
        time_ok = _aware(projected_arrival) <= _aware(deliver_by)
        if time_ok:
            margin = _mins(_aware(deliver_by) - _aware(projected_arrival))
            time_reason = f"On time: arrives with {margin:.0f} min to spare before the deadline."
        else:
            over = _mins(_aware(projected_arrival) - _aware(deliver_by))
            time_reason = f"Infeasible on time: arrives {over:.0f} min past the deadline."

    charging_required = num_stops > 0 or not plan.can_reach
    reasons: list[str] = []

    # --- Range gate ---
    if not plan.can_reach:
        gap = (
            f" (would strand near mile {plan.stranded_at_mi:.0f})"
            if plan.stranded_at_mi is not None else ""
        )
        reasons.append(
            f"Out of range: needs {energy_req:.0f} kWh but only {usable:.0f} kWh is "
            f"available above the {params.reserve_pct:.0f}% reserve, and no reachable "
            f"corridor charger closes the gap{gap}."
        )
        verdict = Verdict.INFEASIBLE
        on_time = False
    else:
        if num_stops == 0:
            reasons.append(
                f"Within range: needs {energy_req:.0f} kWh, has {usable:.0f} kWh above the "
                f"{params.reserve_pct:.0f}% reserve — no charging needed."
            )
        else:
            stop_word = "stop" if num_stops == 1 else "stops"
            reasons.append(
                f"Reaches with {num_stops} charge {stop_word}: +{energy_to_add:.0f} kWh "
                f"total (~{charge_hours * 60:.0f} min) along the route."
            )
        # --- Time gate ---
        reasons.append(time_reason)
        on_time = time_ok
        if time_ok:
            verdict = Verdict.FEASIBLE if num_stops == 0 else Verdict.FEASIBLE_WITH_CHARGING
        else:
            verdict = Verdict.INFEASIBLE

    return Assessment(
        verdict=verdict,
        reasons=tuple(reasons),
        consumption_kwh_per_mi=consumption,
        energy_required_kwh=energy_req,
        usable_energy_for_trip_kwh=usable,
        charging_required=charging_required,
        num_charge_stops=num_stops,
        stops=stops,
        stranded_at_mi=stranded_at_mi,
        energy_to_add_kwh=energy_to_add,
        charge_time_hours=charge_hours,
        charge_cost_usd=cost,
        drive_hours=drive_hours,
        dwell_hours=dwell_hours,
        total_hours=total_hours,
        time_mode=time_mode,
        latest_departure=latest_departure,
        projected_arrival=projected_arrival,
        now_reference=now_reference,
        on_time=on_time,
    )
