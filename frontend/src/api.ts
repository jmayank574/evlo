import type {
  Assessment,
  AssessParams,
  FleetResponse,
  Load,
  Methodology,
  Truck,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  config: () => get<{ mapbox_public_token: string }>("/api/config"),
  trucks: () => get<Truck[]>("/api/trucks"),
  loads: () => get<Load[]>("/api/loads"),
  methodology: () => get<Methodology>("/api/methodology"),
  assess: async (body: {
    truck_id: string;
    load_id: string;
    soc_start_pct: number;
    params?: AssessParams;
  }): Promise<Assessment> => {
    const res = await fetch(`${BASE}/api/assess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json() as Promise<Assessment>;
  },
  assessFleet: async (body: {
    load_id: string;
    soc_start_pct: number;
    params?: AssessParams;
    time_mode?: string;
    depart_at?: string;
  }): Promise<FleetResponse> => {
    const res = await fetch(`${BASE}/api/assess/fleet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json() as Promise<FleetResponse>;
  },
};
