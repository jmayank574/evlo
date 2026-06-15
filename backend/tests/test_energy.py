"""Exhaustive known-input/known-output tests for the pure energy model.

This is the part of Voltpath that must be provably correct, so every function
and every verdict branch is pinned to hand-computed expected values.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.energy import (
    ARRIVE_BY,
    Assessment,
    ChargeOption,
    ModelParams,
    TruckSpec,
    Verdict,
    assess,
    charge_cost_usd,
    charge_time_hours,
    consumption_kwh_per_mi,
    effective_charge_power_kw,
    energy_required_kwh,
    plan_charging,
    usable_energy_for_trip_kwh,
)

# A clean synthetic truck for exact arithmetic (not a product record).
TRUCK = TruckSpec(
    name="TestTruck",
    usable_kwh=800.0,
    base_consumption_kwh_per_mi=1.6,
    reference_payload_lb=40_000.0,
    max_charge_kw=1000.0,
    gvwr_lb=82_000.0,
)

PARAMS = ModelParams()  # defaults: reserve 15%, dwell 30 min, k=0.025, eff 0.92, cap 80%, $0.20


# --------------------------------------------------------------------------- #
# consumption_kwh_per_mi
# --------------------------------------------------------------------------- #


def test_consumption_at_reference_payload_equals_base():
    assert consumption_kwh_per_mi(TRUCK, 40_000.0, PARAMS) == pytest.approx(1.6)


def test_consumption_increases_with_heavier_payload():
    # +4000 lb = +2 tons -> +2 * 0.025 = +0.05
    assert consumption_kwh_per_mi(TRUCK, 44_000.0, PARAMS) == pytest.approx(1.65)


def test_consumption_decreases_with_lighter_payload():
    # -10000 lb = -5 tons -> -0.125
    assert consumption_kwh_per_mi(TRUCK, 30_000.0, PARAMS) == pytest.approx(1.475)


def test_consumption_clamped_non_negative():
    light = TruckSpec("L", 800, 0.05, 40_000, 1000, 82_000)
    # huge negative delta would drive it negative; must clamp to 0
    assert consumption_kwh_per_mi(light, 0.0, PARAMS) == 0.0


def test_payload_coefficient_is_tunable():
    p = ModelParams(payload_coefficient_kwh_per_mi_per_ton=0.05)
    # +2 tons * 0.05 = +0.10
    assert consumption_kwh_per_mi(TRUCK, 44_000.0, p) == pytest.approx(1.70)


# --------------------------------------------------------------------------- #
# energy_required_kwh
# --------------------------------------------------------------------------- #


def test_energy_required_is_consumption_times_distance():
    # 1.65 kWh/mi * 100 mi = 165 kWh
    assert energy_required_kwh(TRUCK, 44_000.0, 100.0, PARAMS) == pytest.approx(165.0)


# --------------------------------------------------------------------------- #
# usable_energy_for_trip_kwh
# --------------------------------------------------------------------------- #


def test_usable_energy_respects_reserve():
    # 800 * (100 - 15)/100 = 680
    assert usable_energy_for_trip_kwh(TRUCK, 100.0, PARAMS) == pytest.approx(680.0)


def test_usable_energy_partial_soc():
    # 800 * (50 - 15)/100 = 280
    assert usable_energy_for_trip_kwh(TRUCK, 50.0, PARAMS) == pytest.approx(280.0)


def test_usable_energy_below_reserve_is_zero():
    assert usable_energy_for_trip_kwh(TRUCK, 10.0, PARAMS) == 0.0


# --------------------------------------------------------------------------- #
# charging helpers
# --------------------------------------------------------------------------- #


def test_effective_charge_power_is_limited_by_weaker_side():
    assert effective_charge_power_kw(TRUCK, 350.0) == 350.0  # station limits
    weak = TruckSpec("W", 800, 1.6, 40_000, 250.0, 82_000)
    assert effective_charge_power_kw(weak, 350.0) == 250.0  # truck limits


def test_charge_time_hours():
    assert charge_time_hours(210.0, 350.0) == pytest.approx(0.6)  # 36 min


def test_charge_time_zero_when_nothing_to_add():
    assert charge_time_hours(0.0, 350.0) == 0.0


def test_charge_time_raises_on_zero_power():
    with pytest.raises(ValueError):
        charge_time_hours(100.0, 0.0)


def test_charge_cost_accounts_for_losses_and_is_decimal():
    # add 100 kWh / 0.92 efficiency = 108.6957 grid kWh * $0.20 = $21.74
    cost = charge_cost_usd(100.0, PARAMS)
    assert isinstance(cost, Decimal)
    assert cost == Decimal("21.74")


def test_charge_cost_zero_when_nothing_to_add():
    assert charge_cost_usd(0.0, PARAMS) == Decimal("0.00")


# --------------------------------------------------------------------------- #
# assess() — the three-state verdict, every branch
# --------------------------------------------------------------------------- #

DEPART = datetime(2026, 6, 14, 8, 0)


def test_verdict_feasible_no_charging_on_time():
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=100.0,
        drive_hours=2.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 12, 0),
        soc_start_pct=100.0,
        params=PARAMS,
    )
    assert a.verdict == Verdict.FEASIBLE
    assert a.charging_required is False
    assert a.energy_required_kwh == pytest.approx(165.0)
    assert a.total_hours == pytest.approx(2.5)  # 2h drive + 0.5h dwell
    assert a.projected_arrival == datetime(2026, 6, 14, 10, 30)
    assert a.on_time is True
    assert a.charge_cost_usd == Decimal("0.00")


def test_verdict_infeasible_time_only_within_range():
    # Plenty of range, but the drive alone overruns the window.
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=50.0,
        drive_hours=10.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 13, 0),  # only 5h window
        soc_start_pct=100.0,
        params=PARAMS,
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert a.charging_required is False
    assert a.on_time is False
    assert "past the deadline" in a.reasons[-1]


def test_verdict_feasible_with_charging_one_stop():
    # soc 30 -> start 240 kWh; reachable charger at mile 50 -> add 210, 36 min.
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=200.0,
        drive_hours=4.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 14, 0),  # 6h window
        soc_start_pct=30.0,
        params=PARAMS,
        corridor=[ChargeOption("c1", 50.0, 350.0)],
    )
    assert a.verdict == Verdict.FEASIBLE_WITH_CHARGING
    assert a.num_charge_stops == 1
    assert a.energy_to_add_kwh == pytest.approx(210.0)  # charges just enough to finish
    assert a.charge_time_hours == pytest.approx(0.6)
    assert a.total_hours == pytest.approx(5.1)  # 4 + 0.6 + 0.5
    assert a.projected_arrival == datetime(2026, 6, 14, 13, 6)
    assert a.on_time is True
    assert a.charge_cost_usd == Decimal("45.65")  # 210/0.92*0.20


def test_verdict_infeasible_out_of_range_no_charger():
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=200.0,
        drive_hours=4.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 14, 0),
        soc_start_pct=30.0,
        params=PARAMS,
        corridor=[],  # no corridor chargers
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert a.charging_required is True
    assert a.num_charge_stops == 0
    assert "no reachable corridor charger" in a.reasons[0]


def test_verdict_infeasible_charging_busts_window():
    # Same as the one-stop case, but a tighter window.
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=200.0,
        drive_hours=4.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 12, 30),  # 4.5h window, need 5.1h
        soc_start_pct=30.0,
        params=PARAMS,
        corridor=[ChargeOption("c1", 50.0, 350.0)],
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert a.num_charge_stops == 1  # charge was planned
    assert a.charge_time_hours == pytest.approx(0.6)
    assert a.on_time is False
    assert "past the deadline" in a.reasons[-1]


def test_verdict_feasible_with_multiple_stops():
    # Long run, low SoC: needs more than one stop. Chargers spaced along route.
    a = assess(
        truck=TRUCK,  # usable 800, ~1.65 kWh/mi at 44k lb
        payload_lb=44_000.0,
        distance_mi=900.0,
        drive_hours=15.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 16, 12, 0),  # generous window
        soc_start_pct=40.0,
        params=PARAMS,
        corridor=[ChargeOption(f"c{m}", float(m), 350.0) for m in range(100, 900, 100)],
    )
    assert a.verdict == Verdict.FEASIBLE_WITH_CHARGING
    assert a.num_charge_stops >= 2
    assert len(a.stops) == a.num_charge_stops
    # stops are ordered along the route
    alongs = [s.along_mi for s in a.stops]
    assert alongs == sorted(alongs)


def test_verdict_infeasible_charger_gap_strands():
    # One reachable charger early, then a long gap with nothing -> stranded.
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=900.0,
        drive_hours=15.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 17, 0, 0),
        soc_start_pct=40.0,
        params=PARAMS,
        corridor=[ChargeOption("early", 100.0, 350.0)],  # nothing after mile 100
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert "strand" in a.reasons[0].lower() or "no reachable" in a.reasons[0]


def test_unknown_power_chargers_are_not_usable():
    # A corridor charger with no known power can't be used (we never invent kW).
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=200.0,
        drive_hours=4.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 20, 0),
        soc_start_pct=30.0,
        params=PARAMS,
        corridor=[ChargeOption("no-power", 50.0, 0.0)],
    )
    assert a.verdict == Verdict.INFEASIBLE  # the only charger is unusable
    assert a.num_charge_stops == 0


def test_arrive_by_computes_latest_departure_and_is_feasible():
    # 100 mi @ 1.65 = no charging; trip = 2h drive + 0.5h dwell = 2.5h.
    # deadline 12:00 -> latest departure 09:30; pickup opens 06:00, now 05:00 -> feasible.
    a = assess(
        truck=TRUCK, payload_lb=44_000.0, distance_mi=100.0, drive_hours=2.0,
        deliver_by=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        soc_start_pct=100.0, params=PARAMS, time_mode=ARRIVE_BY,
        pickup_window_start=datetime(2026, 6, 14, 6, 0, tzinfo=timezone.utc),
        now=datetime(2026, 6, 14, 5, 0, tzinfo=timezone.utc),
    )
    assert a.verdict == Verdict.FEASIBLE
    assert a.time_mode == ARRIVE_BY
    assert a.latest_departure == datetime(2026, 6, 14, 9, 30, tzinfo=timezone.utc)
    assert a.on_time is True
    assert "slack" in a.reasons[-1]
    # reasons must NOT bake absolute clock times (rendered by the frontend in local tz)
    assert "AM" not in a.reasons[-1] and "PM" not in a.reasons[-1]


def test_arrive_by_latest_departure_equals_deadline_minus_trip():
    """Pin the one roll-by value: latest_departure = deadline - (drive+charge+dwell)."""
    deadline = datetime(2026, 6, 16, 19, 0, tzinfo=timezone.utc)
    a = assess(
        truck=TRUCK, payload_lb=44_000.0, distance_mi=200.0, drive_hours=4.0,
        deliver_by=deadline, soc_start_pct=30.0, params=PARAMS, time_mode=ARRIVE_BY,
        corridor=[ChargeOption("c1", 50.0, 350.0)],  # forces one charge stop
        pickup_window_start=datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc),
        now=datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc),
    )
    assert a.num_charge_stops == 1
    assert a.total_hours == pytest.approx(a.drive_hours + a.charge_time_hours + a.dwell_hours)
    assert a.latest_departure == deadline - timedelta(hours=a.total_hours)


def test_arrive_by_charge_time_pulls_latest_departure_earlier():
    # Same lane but low SoC forces a charge stop; the added charge time must move
    # the latest departure earlier than the no-charge case.
    common = dict(
        truck=TRUCK, payload_lb=44_000.0, distance_mi=200.0, drive_hours=4.0,
        deliver_by=datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc), params=PARAMS,
        time_mode=ARRIVE_BY, corridor=[ChargeOption("c1", 50.0, 350.0)],
        pickup_window_start=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
        now=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
    )
    charged = assess(soc_start_pct=30.0, **common)   # needs a stop (+0.6h)
    full = assess(soc_start_pct=100.0, **common)     # no charging
    assert charged.num_charge_stops == 1 and full.num_charge_stops == 0
    # charged trip is longer -> its latest departure is earlier
    assert charged.latest_departure < full.latest_departure


def test_arrive_by_infeasible_when_latest_departure_in_past():
    # Tight deadline: latest departure lands before 'now' -> time-infeasible.
    a = assess(
        truck=TRUCK, payload_lb=44_000.0, distance_mi=100.0, drive_hours=2.0,
        deliver_by=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),  # latest dep 09:30
        soc_start_pct=100.0, params=PARAMS, time_mode=ARRIVE_BY,
        pickup_window_start=datetime(2026, 6, 14, 6, 0, tzinfo=timezone.utc),
        now=datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc),  # already past 09:30
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert a.on_time is False
    assert "min ago" in a.reasons[-1]


def test_arrive_by_infeasible_when_latest_departure_before_pickup_opens():
    a = assess(
        truck=TRUCK, payload_lb=44_000.0, distance_mi=100.0, drive_hours=2.0,
        deliver_by=datetime(2026, 6, 14, 7, 0, tzinfo=timezone.utc),  # latest dep 04:30
        soc_start_pct=100.0, params=PARAMS, time_mode=ARRIVE_BY,
        pickup_window_start=datetime(2026, 6, 14, 6, 0, tzinfo=timezone.utc),  # opens after 04:30
        now=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
    )
    assert a.verdict == Verdict.INFEASIBLE
    assert "opens for pickup" in a.reasons[-1]


def test_engine_computes_on_usable_not_nameplate():
    """Guard: the engine must size the trip against USABLE energy. If a smaller
    usable value is supplied, an otherwise-feasible run must flip to infeasible.

    (Per the CARB filing, the Tesla Semi Long Range's 822 kWh IS the usable
    figure, so using it is correct. This test pins the *mechanism*: feed 822
    vs a hypothetical 548 usable and the verdict must change — proving the
    engine consumes usable energy, never a larger nameplate.)
    """
    big = TruckSpec("usable-822", 822.0, 1.6, 40_000, 1000.0, 82_000)
    small = TruckSpec("usable-548", 548.0, 1.6, 40_000, 1000.0, 82_000)
    common = dict(
        payload_lb=40_000.0, distance_mi=300.0, drive_hours=5.0, depart_at=DEPART,
        deliver_by=datetime(2026, 6, 15, 0, 0), soc_start_pct=100.0, params=PARAMS,
        corridor=[],
    )
    # 300 mi @ 1.6 = 480 kWh. Usable@822,100%,15% = 698.7 (feasible, no charge).
    a_big = assess(truck=big, **common)
    assert a_big.verdict == Verdict.FEASIBLE
    # Usable@548 = 465.8 < 480 -> needs charging, none reachable -> infeasible.
    a_small = assess(truck=small, **common)
    assert a_small.verdict == Verdict.INFEASIBLE


def test_base_consumption_is_constant_payload_term_is_additive():
    """No double-counting: at the reference payload the marginal payload term is
    exactly zero, so consumption equals the base; the coefficient only adds on
    top for loads heavier than the reference."""
    truck = TruckSpec("t", 800, 1.644, 40_000, 1000, 82_000)
    at_ref = consumption_kwh_per_mi(truck, 40_000.0, PARAMS)
    assert at_ref == pytest.approx(1.644)  # base, untouched by the payload term
    heavier = consumption_kwh_per_mi(truck, 50_000.0, PARAMS)  # +5 tons
    assert heavier == pytest.approx(1.644 + 5 * PARAMS.payload_coefficient_kwh_per_mi_per_ton)


def test_assessment_is_frozen_dataclass():
    a = assess(
        truck=TRUCK,
        payload_lb=44_000.0,
        distance_mi=100.0,
        drive_hours=2.0,
        depart_at=DEPART,
        deliver_by=datetime(2026, 6, 14, 12, 0),
        soc_start_pct=100.0,
        params=PARAMS,
    )
    assert isinstance(a, Assessment)
    with pytest.raises(Exception):
        a.verdict = Verdict.INFEASIBLE  # type: ignore[misc]
