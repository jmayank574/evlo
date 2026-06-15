"""Builds the Methodology payload: every model knob with its value, units, and
whether it's an estimate, plus the real data sources. This is the transparency
surface — the skeptical-founder panel — so it must mirror reality exactly.
"""

from __future__ import annotations

from app.domain.energy import ModelParams
from app.schemas import MethodologyOut, ParamDoc, SourceDoc


def build_methodology(params: ModelParams | None = None) -> MethodologyOut:
    p = params or ModelParams()
    param_docs = [
        ParamDoc(
            name="reserve_pct", value=p.reserve_pct, unit="%",
            description="Battery the truck must still have on arrival (safety reserve).",
            is_estimate=False,
        ),
        ParamDoc(
            name="dwell_buffer_min", value=p.dwell_buffer_min, unit="min",
            description="Fixed handling time added to every trip (loading, gate, inspection).",
            is_estimate=False,
        ),
        ParamDoc(
            name="payload_coefficient_kwh_per_mi_per_ton",
            value=p.payload_coefficient_kwh_per_mi_per_ton, unit="kWh/mi per ton",
            description=(
                "ESTIMATE — extra energy per mile for each ton of payload above the "
                "truck's reference payload. Derived from rolling-resistance physics and "
                "the ~51 Wh/ton-mile figure (arXiv:1804.05974); no Nevoya telemetry. "
                "Turn this knob to see how sensitive the verdict is to it."
            ),
            is_estimate=True,
        ),
        ParamDoc(
            name="charge_efficiency", value=p.charge_efficiency, unit="fraction",
            description="Share of grid energy that reaches the battery (charging losses). Affects cost only.",
            is_estimate=True,
        ),
        ParamDoc(
            name="charge_soc_cap_pct", value=p.charge_soc_cap_pct, unit="%",
            description=(
                "We model charging only up to this SoC. The slower high-SoC taper above it "
                "is NOT modeled (a stated v1 simplification)."
            ),
            is_estimate=False,
        ),
        ParamDoc(
            name="energy_price_per_kwh_usd", value=float(p.energy_price_per_kwh_usd), unit="$/kWh",
            description="Price used for charge-cost estimates.",
            is_estimate=False,
        ),
    ]

    sources = [
        SourceDoc(name="Freightliner eCascadia specs", trust="manufacturer",
                  url="https://www.freightliner.com/trucks/ecascadia/specifications/",
                  used_for="eCascadia battery/range/GCW/charge power"),
        SourceDoc(name="Volvo VNR Electric", trust="manufacturer + secondary",
                  url="https://www.volvotrucks.us/trucks/vnr-electric/",
                  used_for="VNR Electric range/charge; usable kWh via secondary source"),
        SourceDoc(name="Tesla Semi (CARB filing)", trust="regulatory",
                  url="https://electrek.co/2026/05/08/tesla-semi-battery-size-822-kwh-548-kwh-carb-official/",
                  used_for="Tesla Semi 822/548 kWh battery"),
        SourceDoc(name="NACFE Run on Less – Electric DEPOT", trust="measured",
                  url="https://nacfe.org/research/run-on-less/run-on-less-electric-depot/",
                  used_for="Real-world 1.55–1.72 kWh/mi consumption cross-check"),
        SourceDoc(name="Economic Case for Electric Semi-Trucks (arXiv)", trust="measured/analytical",
                  url="https://arxiv.org/pdf/1804.05974",
                  used_for="Payload sensitivity (~51 Wh/ton-mile) and full-load reference"),
        SourceDoc(name="NREL/NLR Alternative Fuels Data Center", trust="government",
                  url="https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/",
                  used_for="Real charging-station locations (DC fast)"),
        SourceDoc(name="Open Charge Map", trust="community/verified",
                  url="https://openchargemap.org/site/develop/api",
                  used_for="Charger connector power (kW) detail"),
        SourceDoc(name="Mapbox Directions", trust="commercial",
                  url="https://docs.mapbox.com/api/navigation/directions/",
                  used_for="Real road distance and drive time"),
    ]

    notes = [
        "base_consumption per truck is DERIVED as usable_kWh / published_range; the Tesla "
        "result (1.64 kWh/mi) cross-checks NACFE's measured 1.55–1.72 kWh/mi.",
        "reference_payload (40,000 lb) is an ASSUMPTION — OEMs don't publish the payload "
        "their range rating assumes. It is tunable and flagged, never presented as fact.",
        "Charging is modeled as constant power to the SoC cap; the high-SoC taper is omitted.",
        "Corridor charger search samples the route geometry and may miss chargers between "
        "sample points (v1 simplification). NREL/NLR does not publish kW, so charge-time "
        "power comes from Open Charge Map; we never invent a kW value.",
        "The only synthetic data in the system is the sample load roster (badged 'synthetic').",
    ]
    return MethodologyOut(params=param_docs, sources=sources, notes=notes)
