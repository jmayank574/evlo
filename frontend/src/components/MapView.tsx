import { useEffect, useRef } from "react";
import mapboxgl from "mapbox-gl";
import type { Assessment } from "../types";

interface Props {
  token: string;
  assessment: Assessment | null;
}

export function MapView({ token, assessment }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const loadedRef = useRef(false);
  const animRef = useRef<number | null>(null);

  // Initialize the map once.
  useEffect(() => {
    if (!token || !containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = token;
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: "mapbox://styles/mapbox/light-v11",
      center: [-119.4, 35.5],
      zoom: 5,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      loadedRef.current = true;
      map.addSource("route", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [] } },
      });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#12a36b", "line-width": 4, "line-opacity": 0.85 },
      });
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
    };
  }, [token]);

  // Redraw whenever the assessment changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !assessment) return;

    const VERDICT_COLOR: Record<string, string> = {
      feasible: "#12a36b",
      feasible_with_charging: "#9a6a12",
      infeasible: "#b3261e",
    };

    const draw = () => {
      const geom = assessment.route_geometry;
      const coords = (geom?.coordinates ?? []) as [number, number][];
      const src = map.getSource("route") as mapboxgl.GeoJSONSource | undefined;
      if (map.getLayer("route-line")) {
        map.setPaintProperty("route-line", "line-color", VERDICT_COLOR[assessment.verdict] ?? "#12a36b");
      }
      // Animate the route "drawing in" by progressively revealing vertices.
      if (animRef.current) cancelAnimationFrame(animRef.current);
      if (src) {
        if (coords.length < 2) {
          src.setData({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } });
        } else {
          const DURATION = 650;
          const t0 = performance.now();
          const step = (now: number) => {
            const p = Math.min(1, (now - t0) / DURATION);
            const n = Math.max(2, Math.floor(p * coords.length));
            src.setData({
              type: "Feature",
              properties: {},
              geometry: { type: "LineString", coordinates: coords.slice(0, n) },
            });
            if (p < 1) animRef.current = requestAnimationFrame(step);
          };
          animRef.current = requestAnimationFrame(step);
        }
      }

      // Reset markers.
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = [];

      const bounds = new mapboxgl.LngLatBounds();

      if (coords.length) {
        const ls = assessment.load_snapshot as Record<string, unknown>;
        const ends: [[number, number], string, string][] = [
          [coords[0], "origin", `A · ${String(ls.origin_label ?? "Origin")}`],
          [coords[coords.length - 1], "dest", `B · ${String(ls.dest_label ?? "Destination")}`],
        ];
        for (const [pt, cls, label] of ends) {
          const el = document.createElement("div");
          el.className = `map-end ${cls}`;
          const dot = document.createElement("span");
          dot.className = "d";
          const tag = document.createElement("span");
          tag.className = "t";
          tag.textContent = label;
          el.append(dot, tag);
          markersRef.current.push(
            new mapboxgl.Marker({ element: el, anchor: "left" }).setLngLat(pt).addTo(map),
          );
          bounds.extend(pt);
        }
      }

      for (const c of assessment.chargers_used) {
        const el = document.createElement("div");
        el.className = "map-charger-pin";
        el.textContent = String(c.order);
        const label = `Stop ${c.order}: ${c.name ?? c.network ?? "Charger"}${
          c.max_power_kw ? ` · ${c.max_power_kw} kW` : ""
        } · +${c.energy_added_kwh.toFixed(0)} kWh / ${Math.round(c.charge_minutes)} min`;
        const marker = new mapboxgl.Marker(el)
          .setLngLat([c.lon, c.lat])
          .setPopup(new mapboxgl.Popup({ offset: 14 }).setText(label))
          .addTo(map);
        markersRef.current.push(marker);
        bounds.extend([c.lon, c.lat]);
      }

      if (!bounds.isEmpty()) {
        map.fitBounds(bounds, { padding: 70, maxZoom: 9, duration: 600 });
      }
    };

    if (loadedRef.current) draw();
    else map.once("load", draw);
  }, [assessment]);

  return <div id="map" ref={containerRef} />;
}
