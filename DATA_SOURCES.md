# DATA_SOURCES.md

Every external source Voltpath relies on. The rule: **real, citable data is the default; synthetic data is the rare, explicitly-labeled exception.** Every number used in the product traces back to a row here.

Legend for `Trust`:
- `manufacturer` — published by the OEM on an official spec page.
- `regulatory` — filed with a government body (e.g. CARB).
- `measured` — observed real-world operating data from a credible study.
- `secondary` — reputable third-party reporting, used only where the primary source is not (yet) accessible. Flagged in-product.
- `synthetic` — invented by us for demonstration. Always badged in the UI and carries `data_source: "synthetic"` in code.

---

## 1. EV Truck Specifications

### Freightliner eCascadia
- **Source:** https://www.freightliner.com/trucks/ecascadia/specifications/
- **Accessed:** 2026-06-14
- **Trust:** manufacturer
- **Used for:** truck seed record (battery, range, GCW, charge power).
- **Values:**
  - Usable battery: **291 kWh** or **438 kWh**
  - Range: 230 mi (single-drive long-range) / 155 mi (single-drive standard) / 220 mi (tandem)
  - GVWR/GCW: 65,000 lb (single drive) / 82,000 lb (tandem drive)
  - Charge power: up to **180 kW** single-port, up to **270 kW** dual-port; 80% in ~90 min
  - Horsepower: 320–470 HP depending on axle config

### Volvo VNR Electric
- **Source (official):** https://www.volvotrucks.us/trucks/vnr-electric/
- **Source (usable-kWh figure, secondary):** https://electrek.co/2022/01/14/volvo-trucks-introduces-second-generation-vnr-electric-with-bigger-battery-added-range-and-new-configurations/
- **Accessed:** 2026-06-14
- **Trust:** manufacturer (range/charge/GCW) + secondary (usable kWh)
- **Used for:** truck seed record.
- **Values:**
  - Installed battery: **565 kWh** (6-battery) / 375 kWh (4-battery)
  - Usable energy: **452 kWh** (6-battery) — *secondary source, flagged; official usable figure still being sought*
  - Range: up to 275 mi (6-battery) / up to 230 mi (4×2 straight)
  - GCW: up to 82,000 lb (6×2 tractor)
  - Charge power: **250 kW** CCS1; 80% in 60 min (4-batt) / 90 min (6-batt)

### Tesla Semi
- **Source:** CARB regulatory filing (May 2026), reported by:
  - https://electrek.co/2026/05/08/tesla-semi-battery-size-822-kwh-548-kwh-carb-official/
  - https://insideevs.com/news/795266/tesla-semi-official-battery-capacity/
- **Accessed:** 2026-06-14
- **Trust:** regulatory (battery) + manufacturer (range/charge)
- **Used for:** truck seed record.
- **Values:**
  - Battery: **822 kWh USABLE** (Long Range trim) / **548 kWh** (Standard Range — a *separate trim*, not the usable figure of the 822 truck). Both reports state the figure is **usable** capacity: Electrek — "the Long Range Semi carries an **822 kWh usable** battery pack while the Standard Range comes in at 548 kWh"; InsideEVs — "Long Range: **822-kilowatt-hour usable** battery capacity." No separate nameplate figure is disclosed. **The feasibility engine therefore correctly uses 822 as usable for the Long Range truck; 548 belongs to a different vehicle.**
  - Range: **500 mi** at 82,000 lb GCW (Long Range)
  - Charge power: up to **1.2 MW** (Megacharger); ~60% in 30 min
  - GCW: 82,000 lb

---

## 2. Energy Consumption (kWh/mi) grounding for the model

### NACFE — Run on Less – Electric DEPOT (measured, real-world)
- **Source:** https://nacfe.org/research/run-on-less/run-on-less-electric-depot/
- **Cross-ref (ArcBest/Tesla Semi pilot):** https://www.ttnews.com/articles/tesla-semi-arcbest-pilot
- **Accessed:** 2026-06-14
- **Trust:** measured
- **Used for:** calibrating / sanity-checking base consumption.
- **Values:** Tesla Semi observed **1.55–1.72 kWh/mi** in real operation (1.61 kWh/mi PepsiCo; 1.55 kWh/mi ArcBest overall; 1.72 kWh/mi at >50 mph).

### "Quantifying the Economic Case for Electric Semi-Trucks" (full-load reference + ton-mile basis)
- **Source:** https://arxiv.org/pdf/1804.05974
- **Accessed:** 2026-06-14
- **Trust:** measured/analytical (peer-reviewed preprint)
- **Used for:** payload-sensitivity grounding (the `k` coefficient) and the full-load consumption reference.
- **Values:** Class 8 BEV full-load consumption **2.05 ± 0.32 kWh/mi**, equivalent to **≈ 51 Wh/ton-mile**.

### Derivation note — `base_consumption_kwh_per_mi` (how the seed value is computed)
- **Method:** `base_consumption_kwh_per_mi = usable_kwh / published_range_mi` — transparent arithmetic on the published figures above. Carries trust label `derived` (not `manufacturer`).
- **Values:** eCascadia 438/220 = **1.991**; VNR Electric 452/275 = **1.644**; Tesla Semi 822/500 = **1.644**.
- **Cross-check:** the Tesla derived 1.644 kWh/mi sits inside NACFE's *measured* 1.55–1.72 kWh/mi band — independent corroboration that the derivation is sound.
- **`reference_payload_lb`:** set to **40,000 lb** with trust label `assumption`. No OEM publishes the payload its range rating assumes; this is flagged in-product and is tunable in the Methodology panel, never presented as fact.

### NREL heavy-duty deep dive (supporting)
- **Source:** https://docs.nrel.gov/docs/fy23osti/81308.pdf
- **Accessed:** 2026-06-14
- **Trust:** measured/analytical
- **Used for:** corroborating the weight↔energy relationship; >2 kWh/mi for heavy-duty.

---

## 3. APIs (live integrations — keys provisioned by operator, never committed)

### NREL / NLR Alternative Fuels Data Center (AFDC) — charging stations
- **Source / docs:** https://developer.nlr.gov/docs/transportation/alt-fuel-stations-v1/
- **Key:** https://developer.nlr.gov/ (free)
- **Used for:** real, current US charging-station locations (`fuel_type=ELEC`, DC fast / high-power).
- **Domain transition (logged for provenance accuracy):** NREL rebranded to the
  **National Laboratory of the Rockies (NLR)** and **retired the `nrel.gov`
  domain on 2026-05-29**. The API moved from `developer.nrel.gov` to
  **`developer.nlr.gov`**; **existing API keys are unchanged**, and the dataset
  (AFDC) is the same government source. Verified 2026-06-14: old domain no longer
  resolves, new `.gov` domain serves identical AFDC schema, and the transition is
  documented at https://developer.nlr.gov/docs/nlr-domain-transition/.
  The adapter's stored `source` label remains `"NREL"` for historical-row
  continuity; this note records the rename so the trail doesn't look stale.

### Open Charge Map — charging stations (cross-check)
- **Source / docs:** https://openchargemap.org/site/develop/api
- **Used for:** corroborating NREL; connector/power-level detail.

### Mapbox Directions — routing
- **Source / docs:** https://docs.mapbox.com/api/navigation/directions/
- **Used for:** real road distance + drive-time. Server-side only (token kept on backend); a separate public `pk` token is used frontend-only for map display.

---

## 4. Synthetic data (the only invented data in the product)

### Sample load roster (CA / TX lanes)
- **Trust:** synthetic
- **Why synthetic:** no public dataset of an electric carrier's actual freight exists.
- **Handling:** every record carries `data_source: "synthetic"` in the DB and renders a "sample data" badge in the UI. Designed for trivial replacement via real CSV upload.
