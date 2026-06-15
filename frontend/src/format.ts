import type { Assessment, Verdict } from "./types";

export function verdictClass(v: Verdict): string {
  return v === "feasible" ? "v-feasible" : v === "feasible_with_charging" ? "v-charging" : "v-infeasible";
}

export function verdictLabel(v: Verdict): string {
  return v === "feasible" ? "Feasible" : v === "feasible_with_charging" ? "Charging" : "Infeasible";
}

/** minutes -> "2h 10m" / "45m" / "on the dot" */
export function fmtDur(min: number): string {
  const m = Math.round(Math.abs(min));
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h > 0) return `${h}h ${r}m`;
  return `${r}m`;
}

/** A route's local timezone. Times are a fact about the destination, not the
 * viewer — a CA load's deadline is Pacific wherever you're sitting. Derived from
 * the load's state suffix (all sample loads are single-state CA or TX);
 * per-route IANA resolution from coordinates is the documented follow-up. */
export interface Tz {
  zone: string;
  abbr: string;
}
export function routeTz(label?: string): Tz {
  if (label && /\bTX\b/.test(label)) return { zone: "America/Chicago", abbr: "CT" };
  return { zone: "America/Los_Angeles", abbr: "PT" }; // default Pacific
}

/** "Jun 16, 1:00 PM PT" — always in the route tz, always labeled. */
export function fmtDateTime(iso: string, tz: Tz): string {
  const t = new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: tz.zone,
  });
  return `${t} ${tz.abbr}`;
}

/** "1:00 PM PT" — clock only, in the route tz, labeled. */
export function fmtClock(iso: string, tz: Tz): string {
  const t = new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: tz.zone,
  });
  return `${t} ${tz.abbr}`;
}

/** The single number that decides each row — mode-aware. */
export function deciding(a: Assessment): { value: string; label: string } {
  const arriveBy = a.time_mode === "arrive_by";
  const tz = routeTz(String((a.load_snapshot as Record<string, unknown>).origin_label ?? ""));

  if (a.verdict === "infeasible") {
    const isRange = a.reasons.some((r) => /out of range|strand/i.test(r));
    if (isRange) return { value: "Out of range", label: "no charger closes the gap" };
    if (arriveBy) return { value: "Too late", label: "deadline can't be met" };
    return { value: fmtDur(a.arrival_margin_min ?? 0), label: "past the window" };
  }

  if (arriveBy) {
    return { value: `Roll ${fmtClock(a.latest_departure, tz)}`, label: `${fmtDur(a.departure_slack_min ?? 0)} slack` };
  }
  // depart-at
  if (a.verdict === "feasible_with_charging") {
    return { value: `${a.num_charge_stops}`, label: a.num_charge_stops === 1 ? "charge stop" : "charge stops" };
  }
  return { value: fmtDur(a.arrival_margin_min ?? 0), label: "early" };
}
