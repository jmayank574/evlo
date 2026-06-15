import { describe, expect, it } from "vitest";
import { fmtClock, fmtDateTime, pacificInputToUtcIso, routeTz, toPacificInput } from "./format";

describe("everything renders in Pacific, labeled, never UTC", () => {
  const tz = routeTz();

  it("routeTz is always PT (no per-route CT for now)", () => {
    expect(tz).toEqual({ zone: "America/Los_Angeles", abbr: "PT" });
    expect(routeTz("Dallas, TX")).toEqual({ zone: "America/Los_Angeles", abbr: "PT" });
  });

  it("fmtClock renders the PT wall-clock with a PT label, not UTC", () => {
    // 2026-06-16T13:00:00Z == 6:00 AM PDT
    const s = fmtClock("2026-06-16T13:00:00Z", tz);
    expect(s).toMatch(/6:00\s?AM PT/);
    expect(s).not.toMatch(/UTC|GMT/);
  });

  it("fmtDateTime labels PT and never UTC", () => {
    const s = fmtDateTime("2026-06-16T13:00:00Z", tz);
    expect(s).toContain("PT");
    expect(s).not.toMatch(/UTC|GMT/);
  });
});

describe("depart-at input is Pacific (no UTC path)", () => {
  it("round-trips a Pacific wall-clock through UTC and back", () => {
    // 6:00 AM PT on 2026-06-16 (PDT, UTC-7) == 13:00 UTC
    const iso = pacificInputToUtcIso("2026-06-16T06:00");
    expect(iso).toBe("2026-06-16T13:00:00.000Z");
    expect(toPacificInput(iso)).toBe("2026-06-16T06:00");
  });

  it("arrival reconciles: departure(PT) + trip renders as the expected PT clock", () => {
    // user enters 6:00 AM PT -> 13:00 UTC; + 7h trip = 20:00 UTC == 1:00 PM PT
    const departUtc = pacificInputToUtcIso("2026-06-16T06:00");
    const arrivalUtc = new Date(new Date(departUtc).getTime() + 7 * 3600_000).toISOString();
    expect(fmtClock(arrivalUtc, routeTz())).toMatch(/1:00\s?PM PT/);
  });
});
