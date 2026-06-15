import type { Assessment, Provenance } from "../types";
import { fmtDateTime, fmtDur, routeTz } from "../format";
import { TrustBadge } from "./Badge";

export function VerdictDetail({ a }: { a: Assessment }) {
  const t = a.truck_snapshot as Record<string, unknown>;
  const prov = (t.provenance ?? {}) as Provenance;
  const trust = (f: string) => prov[f]?.trust ?? "—";
  const arriveBy = a.time_mode === "arrive_by";
  const ls = a.load_snapshot as Record<string, unknown>;
  const tz = routeTz(String(ls.origin_label ?? ""));
  const deadline = ls.delivery_window_end ? fmtDateTime(String(ls.delivery_window_end), tz) : null;

  // All absolute times render here, in the viewer's local timezone (one source).
  const timeHeadline =
    a.verdict === "infeasible" && arriveBy && a.departure_slack_min !== null && a.departure_slack_min < 0
      ? `Can't make the ${deadline} delivery — would need to roll before it's possible.`
      : arriveBy
        ? `Roll by ${fmtDateTime(a.latest_departure, tz)}${deadline ? ` to make the ${deadline} delivery.` : "."}`
        : `Arrives ${fmtDateTime(a.projected_arrival, tz)}${deadline ? ` (deadline ${deadline}).` : "."}`;

  return (
    <div className="detail">
      <div className={`time-headline ${arriveBy ? "roll" : "arrive"}`}>{timeHeadline}</div>
      <ul className="reasons">
        {a.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>

      <div className="metrics">
        <div className="metric">
          <div className="k">Route</div>
          <div className="v">{a.route_distance_mi.toFixed(0)} <small>mi</small></div>
        </div>
        <div className="metric">
          <div className="k">Drive</div>
          <div className="v">{fmtDur(a.route_drive_hours * 60)}</div>
        </div>
        <div className="metric">
          <div className="k">{arriveBy ? "Roll by" : "Arrival"}</div>
          <div className="v" style={{ fontSize: 13 }}>
            {fmtDateTime(arriveBy ? a.latest_departure : a.projected_arrival, tz)}
          </div>
        </div>
        <div className="metric">
          <div className="k">Energy need</div>
          <div className="v">{a.energy_required_kwh.toFixed(0)} <small>kWh</small></div>
        </div>
        <div className="metric">
          <div className="k">Usable aboard</div>
          <div className="v">{a.usable_energy_for_trip_kwh.toFixed(0)} <small>kWh</small></div>
        </div>
        <div className="metric">
          <div className="k">{a.charging_required ? "Charge cost" : "On time"}</div>
          <div className="v">
            {a.charging_required ? `$${a.charge_cost_usd.toFixed(2)}` : a.on_time ? "Yes" : "No"}
          </div>
        </div>
      </div>

      {a.chargers_used.length > 0 && (
        <div className="stops">
          <p className="eyebrow" style={{ marginBottom: 8 }}>
            Charge plan — {a.chargers_used.length} stop{a.chargers_used.length > 1 ? "s" : ""},
            +{a.energy_to_add_kwh.toFixed(0)} kWh total
          </p>
          {[...a.chargers_used]
            .sort((x, y) => x.order - y.order)
            .map((s) => (
              <div className="stop" key={s.order}>
                <span className="num">{s.order}</span>
                <div className="info">
                  <div className="nm">{s.name ?? s.network ?? "Corridor charger"}</div>
                  <div className="sub">
                    {s.network ? `${s.network} · ` : ""}
                    {s.max_power_kw ? `${s.max_power_kw} kW · ` : ""}mile {s.along_route_mi.toFixed(0)}
                  </div>
                </div>
                <div className="amt">
                  +{s.energy_added_kwh.toFixed(0)} kWh<br />
                  <span style={{ color: "var(--muted)" }}>{fmtDur(s.charge_minutes)}</span>
                </div>
              </div>
            ))}
        </div>
      )}

      <div className="spec-grid">
        <p className="eyebrow" style={{ margin: "4px 0 2px" }}>Truck specs &amp; provenance</p>
        <div className="spec-line">
          <span>Usable battery</span>
          <span className="sp-val">{String(t.usable_kwh)} kWh <TrustBadge trust={trust("usable_kwh")} /></span>
        </div>
        <div className="spec-line">
          <span>Consumption (base)</span>
          <span className="sp-val">
            {String(t.base_consumption_kwh_per_mi)} kWh/mi <TrustBadge trust={trust("base_consumption_kwh_per_mi")} />
          </span>
        </div>
        <div className="spec-line">
          <span>Max charge power</span>
          <span className="sp-val">{String(t.max_charge_kw)} kW <TrustBadge trust={trust("max_charge_kw")} /></span>
        </div>
      </div>
    </div>
  );
}
