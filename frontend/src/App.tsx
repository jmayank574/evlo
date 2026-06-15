import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { pacificInputToUtcIso, toPacificInput } from "./format";
import type { Assessment, AssessParams, Load, Methodology, TimeMode } from "./types";
import { Controls } from "./components/Controls";
import { FleetList } from "./components/FleetList";
import { MapView } from "./components/MapView";
import { MethodologyPanel } from "./components/MethodologyPanel";

export default function App() {
  const [token, setToken] = useState("");
  const [loads, setLoads] = useState<Load[]>([]);
  const [methodology, setMethodology] = useState<Methodology | null>(null);

  const [loadId, setLoadId] = useState("");
  const [soc, setSoc] = useState(80);
  const [timeMode, setTimeMode] = useState<TimeMode>("arrive_by");
  const [departAt, setDepartAt] = useState(""); // datetime-local (UTC wall-clock)
  const [overrides, setOverrides] = useState<AssessParams>({});

  const [items, setItems] = useState<Assessment[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [cfg, l, m] = await Promise.all([api.config(), api.loads(), api.methodology()]);
        setToken(cfg.mapbox_public_token);
        setLoads(l);
        setMethodology(m);
        if (l.length) {
          setLoadId(l[0].id);
          setDepartAt(toPacificInput(l[0].pickup_window_start)); // PT wall-clock
        }
      } catch (e) {
        setError(`Could not reach the backend. ${(e as Error).message}`);
      }
    })();
  }, []);

  type Opts = { mode?: TimeMode; depart?: string; overrides?: AssessParams };
  async function assessFleet(opts: Opts = {}) {
    if (!loadId) return;
    const mode = opts.mode ?? timeMode;
    const depart = opts.depart ?? departAt;
    const ov = opts.overrides ?? overrides;
    setLoading(true);
    setError(null);
    try {
      const res = await api.assessFleet({
        load_id: loadId,
        soc_start_pct: soc,
        params: Object.keys(ov).length ? ov : undefined,
        time_mode: mode,
        // The input is Pacific wall-clock; send a real UTC instant to the backend.
        depart_at: mode === "depart_at" ? pacificInputToUtcIso(depart) : undefined,
      });
      setItems(res.items);
      setSelectedId(res.items[0]?.id ?? null);
    } catch (e) {
      setError((e as Error).message);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  const debounce = useRef<number | undefined>(undefined);
  function reassessSoon(opts: Opts) {
    if (!items.length) return;
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => assessFleet(opts), 350);
  }

  function onKnob(name: keyof AssessParams, value: number) {
    const next = { ...overrides, [name]: value };
    setOverrides(next);
    reassessSoon({ overrides: next });
  }
  function resetKnobs() {
    setOverrides({});
    if (items.length) assessFleet({ overrides: {} });
  }
  function onTimeMode(m: TimeMode) {
    setTimeMode(m);
    if (items.length) assessFleet({ mode: m });
  }
  function onDepartAt(v: string) {
    setDepartAt(v);
    if (timeMode === "depart_at") reassessSoon({ depart: v });
  }
  function onLoadChange(id: string) {
    setLoadId(id);
    setItems([]);
    setSelectedId(null);
    const l = loads.find((x) => x.id === id);
    if (l) setDepartAt(toPacificInput(l.pickup_window_start)); // PT wall-clock
  }

  const selected = items.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <span className="logo">Volt<span className="spark">path</span></span>
          <span className="tag">EV load feasibility &amp; charging planner</span>
        </div>
        <button className="ghost-btn" onClick={() => setDrawer(true)}>Methodology &amp; data</button>
      </header>

      <div className="main">
        <aside className="panel">
          <Controls
            loads={loads}
            loadId={loadId}
            soc={soc}
            timeMode={timeMode}
            departAt={departAt}
            loading={loading}
            onLoad={onLoadChange}
            onSoc={setSoc}
            onTimeMode={onTimeMode}
            onDepartAt={onDepartAt}
            onAssess={() => assessFleet()}
          />
          {error && <div className="error">{error}</div>}
          {loading && !items.length && <div className="loading">Routing the lane and scanning corridor chargers…</div>}
          {items.length > 0 && <FleetList items={items} selectedId={selectedId} onSelect={setSelectedId} />}
        </aside>

        <div className="map-wrap">
          {token ? <MapView token={token} assessment={selected} /> : null}
          {!selected && (
            <div className="map-empty">
              Pick a load and assess the fleet — the best truck's route and charge
              stops plot here.
            </div>
          )}
        </div>
      </div>

      {drawer && methodology && (
        <MethodologyPanel
          methodology={methodology}
          overrides={overrides}
          onChange={onKnob}
          onReset={resetKnobs}
          onClose={() => setDrawer(false)}
        />
      )}
    </div>
  );
}
