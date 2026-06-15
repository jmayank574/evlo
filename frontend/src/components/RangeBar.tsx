import { useEffect, useState } from "react";
import type { Assessment } from "../types";
import { verdictClass } from "../format";

/**
 * Glanceable per-truck range bar: the full trip with the truck's starting usable
 * range filled, charge stops as ticks, and (when it strands) a red gap to the
 * destination. Lets you see at a glance why #1 clears the lane and #4 doesn't.
 * Nothing here is fabricated — every position comes from the assessment.
 */
export function RangeBar({ a }: { a: Assessment }) {
  const trip = a.route_distance_mi;
  const consumption = a.consumption_kwh_per_mi || 1;
  const startRange = a.usable_energy_for_trip_kwh / consumption; // mi before reserve, no charging
  const pct = (mi: number) => Math.max(0, Math.min(100, (mi / trip) * 100));

  const reaches = a.verdict !== "infeasible" || a.stranded_at_mi == null;
  const reachMi = a.stranded_at_mi == null ? trip : a.stranded_at_mi;
  const fillTarget = reaches ? 100 : pct(reachMi);

  // Animate the fill in from 0 (and re-animate on change).
  const [w, setW] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setW(fillTarget));
    return () => cancelAnimationFrame(id);
  }, [fillTarget]);

  const startNotch = startRange < trip ? pct(startRange) : null;
  const vc = verdictClass(a.verdict);

  return (
    <div className={`rbar-wrap ${vc}`}>
      <div className="rbar">
        <div className="rbar-fill" style={{ width: `${w}%` }} />
        {!reaches && <div className="rbar-gap" style={{ left: `${fillTarget}%` }} />}
        {startNotch != null && a.num_charge_stops > 0 && (
          <div className="rbar-notch" style={{ left: `${startNotch}%` }} title="Range on starting charge" />
        )}
        {a.chargers_used.map((s) => (
          <div
            key={s.order}
            className="rbar-tick"
            style={{ left: `${pct(s.along_route_mi)}%` }}
            title={`Stop ${s.order} · mile ${s.along_route_mi.toFixed(0)}`}
          />
        ))}
      </div>
      <div className="rbar-cap">
        <span>{Math.round(startRange)} mi on charge</span>
        <span>trip {Math.round(trip)} mi</span>
      </div>
    </div>
  );
}
