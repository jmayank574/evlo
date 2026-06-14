# DECISIONS.md

Running log of non-trivial engineering and modeling decisions: the choice, the reason, the tradeoff accepted, and the date. Newest at the bottom of each section.

---

## 2026-06-14 — Project kickoff

### D1. Routing + map provider: Mapbox
- **Choice:** Mapbox Directions (routing) + Mapbox GL (map view).
- **Why:** one provider covers reliable real-road routing *and* the centerpiece map; generous free tier; production-grade (the OSRM public demo server is explicitly not for production). Operator confirmed.
- **Tradeoff:** introduces an API key dependency vs. the keyless OSRM demo. Mitigated by hiding all routing behind a `RoutingProvider` interface so OSRM/Valhalla/Mapbox are swappable.
- **Security detail:** server-side Directions calls use a secret token kept on the backend; the frontend uses a separate **public `pk` token for map display only**.

### D2. External data: live from day one, no fixtures in the product
- **Choice:** integrate NREL, Open Charge Map, and Mapbox against real APIs as soon as keys land. No fabricated/cached fixture data masquerading as real.
- **Why:** the product's credibility is its real data. Operator provisioning all three keys.
- **Tradeoff:** model/API work that touches live data is blocked until keys exist — so we sequence the **pure energy/feasibility model + its unit tests first** (needs no external calls), then wire live adapters. Unit tests use fixed known-input/known-output cases; that is standard TDD, not fabricated product data.
- **Caching:** Directions and charger-API responses are cached (DB/TTL) so a feasibility check doesn't burn free-tier quota on every call.

### D3. Seed fleet: all three real trucks
- **Choice:** Freightliner eCascadia, Volvo VNR Electric, Tesla Semi.
- **Why:** spans the relevant Class 8 BEV range; Tesla Semi additionally has real NACFE *measured* consumption data, anchoring the energy model.
- **Tradeoff:** Volvo's usable-kWh is a secondary source; flagged in data and UI until an official figure is found.

### D4. Money & energy as Decimal where it's currency
- **Choice:** `Numeric`/`Decimal` for dollar amounts (energy cost). Physics quantities (kWh, miles, hours) computed as float, converted to `Decimal` at the cost boundary.
- **Why:** floats are unsafe for money; but Decimal trig/scaling in the physics core is awkward and buys nothing for kWh accuracy.
- **Tradeoff:** a float→Decimal boundary to keep straight. Documented and centralized in the cost function.

### D5. Energy model — transparent physics over opaque fit
- **Payload-adjusted consumption:** `C(p) = C_base + k·(p − p_ref)` where `p` = payload in tons.
  - `C_base` calibrated per-truck to a published/measured kWh/mi at reference payload `p_ref`.
  - `k` (marginal kWh/mi per ton) grounded in rolling-resistance physics and the cited ≈51 Wh/ton-mile figure (arXiv 1804.05974). Exposed as a visible parameter.
- **Reserve:** energy available for the trip = `usable_kwh × (SOC_start − reserve_pct)/100`. `reserve_pct` is a surfaced setting, not a magic number.
- **Charging:** time = energy_to_add ÷ `min(truck_max_kw, station_max_kw) × efficiency`. **Simplification:** modeled as constant power up to an 80% SoC cap; the high-SoC taper is *not* modeled. This is explicitly flagged in the UI. It is conservative on usable range and (slightly) optimistic on charge speed near full — stated, not hidden.
- **Verdict:** `FEASIBLE` / `FEASIBLE_WITH_CHARGING` / `INFEASIBLE`, always returned with the reasons and the underlying numbers so a dispatcher can audit it.
- **Why this approach:** a skeptical founder can read every term. Each assumption is a named parameter surfaced in a Methodology panel.

### D6. Stack
- Backend: FastAPI + PostgreSQL, SQLAlchemy + Alembic, UUID PKs.
- Frontend: React + Vite + TypeScript, Mapbox GL map.
- Deploy: Railway (backend) + Vercel (frontend).

### D7. Truck-spec provenance: per-field JSONB map (operator-confirmed)
- **Choice:** `trucks.provenance` is a JSONB map of `field -> {trust, source_url, accessed_date, note}`.
- **Why:** one truck can mix trust levels — Volvo's range is `manufacturer` but its usable kWh is `secondary`. A single per-row label would force the worst-case onto every field and misrepresent honest data. Per-field keeps the UI from showing a derived/assumed number with false confidence.
- **Tradeoff:** not directly SQL-queryable by trust without JSON operators; acceptable since it's display metadata, not a filter axis.

### D8. Assessment ModelParams snapshot: typed Numeric columns (operator-confirmed)
- **Choice:** each of the 6 ModelParams is its own typed column on `assessments` (Numeric where appropriate), plus jsonb for `chargers_used`/`reasons`/snapshots.
- **Why:** an audit record must be queryable ("show all assessments run with reserve < 10%") and must preserve Decimal precision on price. Typed columns do both.
- **Tradeoff:** adding a future param requires a migration. Fine — params are a small, stable set, and a migration is the honest way to evolve an audit schema.

### D9. No PostGIS
- **Choice:** plain `Numeric(9,6)` lat/lon columns; no PostGIS.
- **Why:** corridor charger search queries the NREL/OCM APIs by lat/lon + radius, not local geo-queries, so PostGIS would be an unused heavy dependency.
- **Tradeoff:** if a real local geospatial need appears, it's a later migration. Cheap to add.

### D10. base_consumption derived; reference_payload an explicit assumption
- **Choice:** `base_consumption_kwh_per_mi = usable_kwh / published_range` (trust `derived`); `reference_payload_lb = 40,000` (trust `assumption`).
- **Why:** no OEM publishes a kWh/mi figure tied to a stated payload. Deriving from published usable-kWh and range is transparent arithmetic, and the Tesla result (1.644) cross-checks cleanly against NACFE's *measured* 1.55–1.72 kWh/mi — corroboration, not coincidence. The payload the rating assumes is genuinely unpublished, so it is flagged `assumption` and surfaced as tunable in the Methodology panel, never shown as fact.
- **Tradeoff:** the absolute kWh/mi inherits whatever payload the OEM rated at; the model's payload term adjusts from `reference_payload`, so the assumption is visible and movable rather than buried.

### D12. NREL → NLR domain change: verified before trusting
- **Choice:** updated the AFDC base URL to `developer.nlr.gov`, but only after verifying the change rather than editing on request.
- **Why:** pointing a real API key at a new domain is a credential-exfiltration risk, and `nlr` vs `nrel` reads like a transposition typo. Before sending the key: (1) confirmed `nrel.gov` no longer resolves while `nlr.gov` does, (2) probed the endpoint with the public `DEMO_KEY` (not the real key) and got valid AFDC data, (3) confirmed `nlr.gov` is a `.gov` (government-controlled, not casually registrable), (4) found the official transition notice (retired 2026-05-29, keys unchanged). Only then used the real key.
- **Tradeoff:** a few minutes of verification vs. blindly trusting an instruction that touches a secret. Worth it every time.

### D11. Local Postgres on host port 5433
- **Choice:** dev Postgres container maps to host `5433` (not 5432).
- **Why:** a native Postgres already occupies 5432 on this machine and was answering first, causing auth failures. 5433 sidesteps the conflict. Production (Railway) injects its own `DATABASE_URL`.
