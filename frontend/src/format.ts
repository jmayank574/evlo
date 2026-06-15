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

/** The whole app is pinned to Pacific for now, labeled "PT" everywhere. Times
 * are a fact about the route, not the viewer. Per-route timezone resolution
 * (e.g. Central for the TX lanes) is a deliberate future follow-up — see
 * DECISIONS D19. There is intentionally no UTC display path. */
const PACIFIC = "America/Los_Angeles";
export interface Tz {
  zone: string;
  abbr: string;
}
export function routeTz(_label?: string): Tz {
  return { zone: PACIFIC, abbr: "PT" };
}

function _parts(d: Date, zone: string): Record<string, string> {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  return dtf.formatToParts(d).reduce<Record<string, string>>((a, x) => {
    a[x.type] = x.value;
    return a;
  }, {});
}

/** Minutes the zone is ahead of UTC at `instant` (negative west of UTC). */
function _zoneOffsetMin(instant: Date, zone: string): number {
  const p = _parts(instant, zone);
  const hh = p.hour === "24" ? 0 : +p.hour;
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, hh, +p.minute, +p.second);
  return (asUtc - instant.getTime()) / 60000;
}

/** UTC ISO -> Pacific wall-clock "YYYY-MM-DDTHH:MM" for a datetime-local input. */
export function toPacificInput(iso: string): string {
  const p = _parts(new Date(iso), PACIFIC);
  const hh = p.hour === "24" ? "00" : p.hour;
  return `${p.year}-${p.month}-${p.day}T${hh}:${p.minute}`;
}

/** Pacific wall-clock "YYYY-MM-DDTHH:MM" (what the user typed) -> UTC ISO instant. */
export function pacificInputToUtcIso(wallclock: string): string {
  const [datePart, timePart] = wallclock.split("T");
  const [y, mo, d] = datePart.split("-").map(Number);
  const [h, mi] = timePart.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const offset = _zoneOffsetMin(new Date(guess), PACIFIC);
  return new Date(guess - offset * 60000).toISOString();
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
    // Just the actionable answer — no "slack" jargon in the glance view.
    return { value: `Roll by ${fmtClock(a.latest_departure, tz)}`, label: "latest departure" };
  }
  // depart-at
  if (a.verdict === "feasible_with_charging") {
    return { value: `${a.num_charge_stops}`, label: a.num_charge_stops === 1 ? "charge stop" : "charge stops" };
  }
  return { value: fmtDur(a.arrival_margin_min ?? 0), label: "early" };
}
