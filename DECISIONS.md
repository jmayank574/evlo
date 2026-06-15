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

### D17. Timezone-consistency fix + relative (non-stale) sample-load dates (2026-06-15)
**Bug 1 — two roll-by times / two deadlines were a tz-rendering split, not a logic bug.** Trace on LD-1003 confirmed exactly one `latest_departure` (= deadline − drive − charge − dwell). The conflict came from the **server baking absolute clock times into reason strings in UTC** (`_clock`) while the **frontend rendered every other datetime in the browser's local tz**. Fix: server reasons now use **tz-independent durations** ("357 min of slack", "36 min past the deadline") and contain **no absolute clock times**; the **frontend is the single renderer** of all absolute times (roll-by, arrival, deadline) in the viewer's local tz, and composes the "Roll by … to make the … delivery" headline itself. Pinned by `test_arrive_by_latest_departure_equals_deadline_minus_trip` and an assertion that reasons carry no "AM/PM".

**Bug 2 — sample-load dates are now relative, resolved at request time.** Synthetic loads store **relative offsets** (hours from today 00:00 UTC) in new nullable columns; absolute window columns are now nullable (used only by real/uploaded loads). `resolve_windows(load, now)` converts offsets → absolute at **every request** (`GET /loads`, assess, slack math, persisted snapshot) — never at seed time (which would just relocate staleness). Verified: resolving against `now` +0/+3/+30/+400 days always yields a future deadline. So a seeded "deliver +1 day 12:00" reads as tomorrow-noon whenever the deployed link is opened.

### D18. Times pinned to the route timezone + labeled; fleet-card layout (2026-06-15)
- **Times are a fact about the destination, not the viewer.** Rendering in the browser's local tz was subtly wrong (an Eastern viewer saw a CA load's 1:00 PM deadline as 4:00 PM). All absolute times now render in the **route's local timezone and are explicitly labeled** ("1:00 PM PT", "Roll 5:05 AM PT", "10:00 AM CT"). The server still emits **durations only**; the frontend is the single renderer (D17), so this was a 2-function change (`fmtDateTime`, `fmtClock` take an IANA zone + abbr) threaded into the three callers.
- **Per-route tz is derived from the load's state suffix** (", TX" → America/Chicago/CT, else America/Los_Angeles/PT) — correct for the single-state sample loads. **Follow-up:** resolve the IANA zone from the route coordinates (handles multi-state lanes and other regions); the state-suffix heuristic is the interim.
- **Fleet card layout:** the roll-by deciding number ("Roll 5:05 AM PT") was fighting the truck name for horizontal space, wrapping the name vertically. Restructured the row: truck name + verdict pill on the top line (name truncates with ellipsis), the deciding number on its **own line** below, range bar under that. The lead row keeps the larger deciding number.
- **Dropped "slack" jargon (operator feedback):** "Roll 4:47 AM PT · 6h 48m slack" was confusing — "slack" measured the departure *window* against the (oddly-timed synthetic) pickup open, which over-explained what the roll-by time already conveys. Glance view now shows just **"Roll by 4:47 AM PT"** (label "latest departure"); server reasons drop "slack"/raw-minutes for plain durations ("a 6h 48m departure window", "arrives 2h 10m past the deadline"). Since all trucks on a load share the deadline, roll-by time alone orders the ranking.

### D16. Departure/arrival time modes (depart-at / arrive-by) (2026-06-14)
- **Two modes off one quantity.** `trip_duration = drive + Σ charge_stop_hours + dwell` has no clock-time dependency (we don't model time-of-day traffic), so both modes derive from it: depart-at → `arrival = depart_at + trip_duration`; arrive-by → `latest_departure = deadline − trip_duration`. **Charge time feeds the backwards (latest-departure) math automatically** — a truck needing more stops has a larger trip_duration and therefore an earlier latest_departure. Verified live: on LD-1008, the 0-stop Tesla LR rolls by 12:40 PM, each charging truck earlier (SR 12:20, Volvo 12:13, eCascadia 11:57).
- **Departure is now explicit**, fixing the old implicit `depart_at = pickup_window_start` assumption (the source of 4 AM arrivals).
- **Range and time are independent gates.** A range-feasible run can still be time-infeasible. Arrive-by feasibility: `latest_departure >= max(now, pickup_open)` (operator-confirmed). Reasons distinguish 'latest departure was N min ago' (now binds) from 'before pickup opens' (window binds). Depart-at: arrival ≤ deadline, else 'arrives N min past the deadline'.
- **Mode-aware deciding number + ranking:** arrive-by ranks by departure slack (roll-by headline); depart-at by arrival margin.
- **Honesty (methodology panel):** latest_departure uses Mapbox FREE-FLOW drive time + dwell only — no time-of-day traffic, no hours-of-service driver breaks. Both would push real departures earlier; flagged as a best case, not fabricated.
- **tz-safety:** datetimes are coerced to UTC-aware for time math (`_aware`) so SQLite-naive (tests) and Postgres-aware (prod) both work; stored `projected_arrival` keeps its natural form so the naive unit-test assertions hold. The depart-at input is treated as UTC.

### D15. Glanceable range bar + honest strand point + motion (2026-06-14)
- **Re-confirmed the correctness gate** (Tesla usable) a second time on re-raise. Per-truck, the engine consumes USABLE: eCascadia 438 (= published usable), Volvo **452** (NOT its 565 nameplate — the proof the engine uses usable), Tesla LR **822** (CARB states this is usable), Tesla SR 548. LD-1008 is correctly FEASIBLE on 822; substituting 548 (a different trim) would wrongly flip it to infeasible. No change made.
- **Surfaced `stranded_at_mi`** (domain → model column + migration → persist → API). It's the farthest mile a truck reaches when it can't complete even with charging. This lets the range bar show *exactly* where a truck falls short instead of approximating — honest, not decorative.
- **Range bar (per fleet row):** trip distance, starting usable range filled, charge stops as ticks, a hatched red gap from the strand point to the destination. Glanceable: you see why #1 clears and #4 strands. Every position derives from the assessment; nothing fabricated.
- **Motion (CSS-only, no new dependency):** route "draws in" via progressive vertex reveal (~650 ms); fleet rows settle/stagger in; range-bar fill animates from 0. Tasteful, comprehension-aiding.
- **Hierarchy:** the lead (best) row gets a larger deciding number so the result reads in one second; verdict color is one source of truth across rows, pills, range bar, map line, and pins.
- Note: no `frontend-design` skill exists in this environment; applied premium operator-tool principles directly.

### D14. Multi-stop charge planning, fleet ranking, and the charger-power floor (2026-06-14)
- **Core interaction is now fleet ranking:** for a selected load, every truck is assessed against a **shared route + corridor** (computed once in `build_load_context`, since both depend only on the load, not the truck — one set of Mapbox/charger calls per load) and ranked best-first. New `/api/assess/fleet`; ranking key = (verdict, num_stops, −arrival_margin).
- **Multi-stop model (replaces single-stop):** greedy range-anxiety planner in `plan_charging` — drive to the **farthest reachable** usable charger, add **just enough to finish** (or fill to the SoC cap and continue), repeat; flag where it would strand. Minimizes stops; not a global optimum (stated). This turned blunt "infeasible (needs >1 stop)" results into real "feasible with N stops" plans with pins on the map.
  - **Why "just enough to finish, else fill to cap":** preserves the correct single-stop economics (a small top-up stays small) while enabling multi-stop. Pinned by tests.
- **Charger-power floor (`min_charger_power_kw`, default 150 kW):** live testing exposed the greedy planner selecting operationally absurd low-power chargers (a 3 kW AC plug → 3,300 min; 50 kW destination chargers → 5+ h). No freight operator charges a Class 8 truck at 3 kW. Modeling only genuine DC-fast charging is the honest fix; the threshold is a visible, tunable parameter, **not** a hidden filter. Excluded chargers are dropped from the *plan*, never invented around.
- **Corridor resilience:** corridor discovery is best-effort per (sample point, provider) — one timeout skips that lookup (partial coverage is already a disclosed simplification, not fabrication), but a *total* outage still fails loudly. The route call remains strictly fail-loud.
- **Lean audit records:** each assessment stores only the **picked stops** (with order, kWh added, minutes) — not the full ~250-charger corridor — keeping records small and the map showing exactly the plan.
- **Tesla Semi Standard Range (548 kWh)** added as a 4th real truck — the honest home for the CARB filing's 548 kWh figure (a separate trim), giving the fleet a real range spread.

### D13. Correctness gate — Tesla usable battery & base-consumption derivation (verified 2026-06-14)
Two correctness checks before UI work; both traced to source.

**(a) Tesla Semi usable energy — 822 kWh is correct, NOT a bug.**
- The CARB filing (May 2026), as reported by both Electrek and InsideEVs, states **822 kWh is the *usable* capacity** of the **Long Range** trim; **548 kWh is a separate *Standard Range* trim**, not the usable figure of the 822 truck. Quotes: Electrek — "822 kWh **usable** battery pack"; InsideEVs — "822-kilowatt-hour **usable** battery capacity." No separate nameplate is published.
- The engine reads `truck.usable_kwh` in `usable_energy_for_trip_kwh` and sizes the trip on it. With 822 that is correct. Swapping in 548 would model a *different vehicle* and make every Long-Range verdict pessimistically wrong — so it was **not** changed.
- Guard test added (`test_engine_computes_on_usable_not_nameplate`): a 300-mi run feasible at usable 822 (available 698.7 kWh) flips to **infeasible** at usable 548 (available 465.8 kWh) — pinning that the engine consumes usable energy, never a larger nameplate.
- If the Standard Range (548 kWh) is wanted in the product, it belongs as its **own seeded truck**, not as a "correction" to the Long Range.

**(b) Base consumption — derived, additive, no double-counting.**
- `base_consumption_kwh_per_mi` is **derived** as `usable_kwh / published_range` (Tesla: 822/500 = **1.644**), i.e. the consumption at the payload the range is rated at (~82,000 lb GCW). It does **not** trace to NACFE as its *source*; NACFE's measured **1.55–1.72 kWh/mi** is a **cross-check**, and 1.644 sits inside that band. Provenance updated to say so accurately (source = Tesla spec/derivation; NACFE = corroboration).
- `consumption_kwh_per_mi = base + k·(payload − reference)/ton` is purely **additive**. At the reference payload the marginal term is exactly 0, so consumption = base; the loaded figure is never reused as the base. No double-counting. Pinned by `test_base_consumption_is_constant_payload_term_is_additive`.
- Reference payload (40,000 lb) ≈ the cargo at 82,000 lb GCW (tractor+trailer ≈ 40k), so base@reference is consistent with where it was derived.

### D12. NREL → NLR domain change: verified before trusting
- **Choice:** updated the AFDC base URL to `developer.nlr.gov`, but only after verifying the change rather than editing on request.
- **Why:** pointing a real API key at a new domain is a credential-exfiltration risk, and `nlr` vs `nrel` reads like a transposition typo. Before sending the key: (1) confirmed `nrel.gov` no longer resolves while `nlr.gov` does, (2) probed the endpoint with the public `DEMO_KEY` (not the real key) and got valid AFDC data, (3) confirmed `nlr.gov` is a `.gov` (government-controlled, not casually registrable), (4) found the official transition notice (retired 2026-05-29, keys unchanged). Only then used the real key.
- **Tradeoff:** a few minutes of verification vs. blindly trusting an instruction that touches a secret. Worth it every time.

### D11. Local Postgres on host port 5433
- **Choice:** dev Postgres container maps to host `5433` (not 5432).
- **Why:** a native Postgres already occupies 5432 on this machine and was answering first, causing auth failures. 5433 sidesteps the conflict. Production (Railway) injects its own `DATABASE_URL`.
