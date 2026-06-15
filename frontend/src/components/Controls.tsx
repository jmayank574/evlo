import type { Load, TimeMode } from "../types";
import { fmtDateTime, routeTz } from "../format";
import { SyntheticBadge } from "./Badge";

interface Props {
  loads: Load[];
  loadId: string;
  soc: number;
  timeMode: TimeMode;
  departAt: string; // datetime-local value (UTC wall-clock)
  loading: boolean;
  onLoad: (id: string) => void;
  onSoc: (v: number) => void;
  onTimeMode: (m: TimeMode) => void;
  onDepartAt: (v: string) => void;
  onAssess: () => void;
}

export function Controls({
  loads, loadId, soc, timeMode, departAt, loading,
  onLoad, onSoc, onTimeMode, onDepartAt, onAssess,
}: Props) {
  const load = loads.find((l) => l.id === loadId);
  const tz = load ? routeTz(load.origin_label) : routeTz();

  return (
    <div>
      <p className="eyebrow">Dispatch a load</p>

      <div className="field">
        <label className="lbl">Load</label>
        <select value={loadId} onChange={(e) => onLoad(e.target.value)}>
          {loads.map((l) => (
            <option key={l.id} value={l.id}>
              {l.reference} — {l.origin_label} → {l.dest_label}
            </option>
          ))}
        </select>
      </div>

      {load && (
        <div className="load-card">
          <div className="load-lane">
            {load.origin_label} <span className="arrow">→</span> {load.dest_label}
          </div>
          <div className="load-meta">
            <span>{(load.weight_lb / 1000).toFixed(1)}k lb</span>
            <span>·</span>
            <span>deliver by {fmtDateTime(load.delivery_window_end, tz)}</span>
            {load.data_source === "synthetic" && <SyntheticBadge />}
          </div>
        </div>
      )}

      <div className="field" style={{ marginTop: 16 }}>
        <label className="lbl">Time basis</label>
        <div className="seg">
          <button
            className={`seg-btn ${timeMode === "arrive_by" ? "on" : ""}`}
            onClick={() => onTimeMode("arrive_by")}
          >
            Arrive by deadline
          </button>
          <button
            className={`seg-btn ${timeMode === "depart_at" ? "on" : ""}`}
            onClick={() => onTimeMode("depart_at")}
          >
            Depart at
          </button>
        </div>
        {timeMode === "arrive_by" ? (
          <div className="hint">
            Computes the latest safe departure to make the delivery deadline.
          </div>
        ) : (
          <div style={{ marginTop: 8 }}>
            <input
              type="datetime-local"
              value={departAt}
              onChange={(e) => onDepartAt(e.target.value)}
            />
            <div className="hint">Departure time (PT). Computes projected arrival.</div>
          </div>
        )}
      </div>

      <div className="field">
        <label className="lbl">Fleet starting battery (SoC)</label>
        <div className="slider-row">
          <input type="range" min={0} max={100} step={1} value={soc} onChange={(e) => onSoc(Number(e.target.value))} />
          <span className="val">{soc}%</span>
        </div>
        <div className="hint">Assumes every truck starts at this charge (no per-truck telemetry).</div>
      </div>

      <button className="btn-primary" onClick={onAssess} disabled={loading}>
        {loading ? "Assessing fleet…" : "Assess fleet"}
      </button>
    </div>
  );
}
