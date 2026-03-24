import { useEffect, useRef, useState, useCallback } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";

interface DisasterZone {
  id: string;
  lat: number;
  lng: number;
  type: "population" | "infrastructure" | "saturation" | "warning";
  radius: number;
  label: string;
  intensity: number;
}

interface UserInput {
  id: string;
  lat: number;
  lng: number;
  message: string;
  timestamp: string;
  author: string;
}

const INITIAL_ZONES: DisasterZone[] = [
  { id: "z1", lat: 42.5, lng: -1.5, type: "population", radius: 18, label: "Zaragoza Hub", intensity: 0.8 },
  { id: "z2", lat: 41.8, lng: 1.5, type: "population", radius: 12, label: "Barcelona Region", intensity: 0.6 },
  { id: "z3", lat: 39.5, lng: -0.4, type: "infrastructure", radius: 22, label: "Valencia Corridor", intensity: 0.9 },
  { id: "z4", lat: 41.6, lng: 2.2, type: "infrastructure", radius: 10, label: "Costa Brava", intensity: 0.5 },
  { id: "z5", lat: 37.4, lng: -3.8, type: "saturation", radius: 25, label: "Málaga Zone", intensity: 0.95 },
  { id: "z6", lat: 36.7, lng: -4.4, type: "saturation", radius: 15, label: "Costa del Sol", intensity: 0.7 },
  { id: "z7", lat: 38.5, lng: -3.0, type: "warning", radius: 10, label: "La Mancha", intensity: 0.4 },
  { id: "z8", lat: 37.8, lng: -1.5, type: "warning", radius: 12, label: "Murcia Alert", intensity: 0.6 },
  { id: "z9", lat: 41.0, lng: 8.9, type: "infrastructure", radius: 16, label: "Sardinia Link", intensity: 0.7 },
];

const INITIAL_INPUTS: UserInput[] = [
  { id: "u1", lat: 42.3, lng: -1.2, message: "Road blocked near A-23 highway. Emergency vehicles rerouting.", timestamp: "2026-03-24T08:12:00Z", author: "Field Agent #12" },
  { id: "u2", lat: 39.8, lng: -0.2, message: "Flooding reported in low-lying areas. Evacuations underway.", timestamp: "2026-03-24T09:45:00Z", author: "Local Authority" },
  { id: "u3", lat: 37.2, lng: -3.6, message: "Power grid intermittent. Backup generators active.", timestamp: "2026-03-24T10:30:00Z", author: "Utility Ops" },
  { id: "u4", lat: 36.8, lng: -4.2, message: "Shelter capacity at 78%. Need additional supplies.", timestamp: "2026-03-24T11:00:00Z", author: "Red Cross Vol." },
  { id: "u5", lat: 38.2, lng: -2.8, message: "Telecommunications restored in sector 4.", timestamp: "2026-03-24T12:15:00Z", author: "Telecom Tech" },
  { id: "u6", lat: 41.5, lng: 2.0, message: "Medical team deployed. 3 injuries reported.", timestamp: "2026-03-24T07:20:00Z", author: "EMT Unit 7" },
];

const ZONE_COLORS: Record<DisasterZone["type"], string> = {
  population: "#4A90D9",
  infrastructure: "#8B5CF6",
  saturation: "#DC2626",
  warning: "#F59E0B",
};

function AnimatedZones({ zones }: { zones: DisasterZone[] }) {
  const map = useMap();
  return (
    <>
      {zones.map((zone) => (
        <CircleMarker
          key={zone.id}
          center={[zone.lat, zone.lng]}
          radius={zone.radius}
          pathOptions={{
            color: ZONE_COLORS[zone.type],
            fillColor: ZONE_COLORS[zone.type],
            fillOpacity: 0.3 + zone.intensity * 0.3,
            weight: 2,
            opacity: 0.7,
          }}
        >
          <Popup>
            <div className="text-sm font-medium">{zone.label}</div>
            <div className="text-xs opacity-70">Intensity: {Math.round(zone.intensity * 100)}%</div>
          </Popup>
        </CircleMarker>
      ))}
    </>
  );
}

export default function DisasterMap() {
  const [zones, setZones] = useState(INITIAL_ZONES);
  const [inputs] = useState(INITIAL_INPUTS);
  const [selectedInput, setSelectedInput] = useState<UserInput | null>(null);
  const [time, setTime] = useState(0);

  // Simulate real-time zone movement
  useEffect(() => {
    const interval = setInterval(() => {
      setTime((t) => t + 1);
      setZones((prev) =>
        prev.map((z) => ({
          ...z,
          lat: z.lat + Math.sin(time * 0.02 + parseFloat(z.id.slice(1))) * 0.008,
          lng: z.lng + Math.cos(time * 0.015 + parseFloat(z.id.slice(1))) * 0.01,
          intensity: Math.max(0.2, Math.min(1, z.intensity + (Math.random() - 0.5) * 0.05)),
          radius: Math.max(8, z.radius + Math.sin(time * 0.03) * 0.5),
        }))
      );
    }, 800);
    return () => clearInterval(interval);
  }, [time]);

  return (
    <div className="relative h-full w-full">
      <MapContainer
        center={[39.5, -2]}
        zoom={6}
        className="h-full w-full"
        zoomControl={false}
        style={{ background: "hsl(220, 20%, 10%)" }}
      >
        <TileLayer
          attribution=""
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        />
        <AnimatedZones zones={zones} />
        {inputs.map((input) => (
          <CircleMarker
            key={input.id}
            center={[input.lat, input.lng]}
            radius={7}
            pathOptions={{
              color: "#ffffff",
              fillColor: "#ffffff",
              fillOpacity: 0.9,
              weight: 2,
              opacity: 1,
            }}
            eventHandlers={{
              click: () => setSelectedInput(input),
            }}
          />
        ))}
      </MapContainer>

      {/* Title */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[1000]">
        <h1 className="text-2xl font-light tracking-[0.4em] text-foreground/80 uppercase">
          ayuda
        </h1>
      </div>

      {/* Legend */}
      <div className="absolute left-4 bottom-1/3 z-[1000] flex flex-col gap-2">
        {[
          { type: "population" as const, label: "Population Hubs", icon: "◉" },
          { type: "infrastructure" as const, label: "Infrastructure", icon: "○" },
          { type: "saturation" as const, label: "Network Saturation", icon: "◇" },
          { type: "warning" as const, label: "Warnings", icon: "△" },
        ].map((item) => (
          <div
            key={item.type}
            className="flex items-center gap-3 rounded-md bg-card/80 backdrop-blur-sm px-4 py-2.5 border border-border/50"
          >
            <span style={{ color: ZONE_COLORS[item.type] }} className="text-base">
              {item.icon}
            </span>
            <span className="text-sm text-foreground/80">{item.label}</span>
          </div>
        ))}
      </div>

      {/* Live indicator */}
      <div className="absolute top-4 right-4 z-[1000] flex items-center gap-2 rounded-md bg-card/80 backdrop-blur-sm px-3 py-1.5 border border-border/50">
        <span className="relative flex h-2.5 w-2.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-accent"></span>
        </span>
        <span className="text-xs text-foreground/70 uppercase tracking-wider">Live</span>
      </div>

      {/* Active zones counter */}
      <div className="absolute top-14 right-4 z-[1000] rounded-md bg-card/80 backdrop-blur-sm px-3 py-2 border border-border/50">
        <div className="text-xs text-muted-foreground uppercase tracking-wider">Active Zones</div>
        <div className="text-xl font-semibold text-foreground">{zones.length}</div>
      </div>

      <div className="absolute top-28 right-4 z-[1000] rounded-md bg-card/80 backdrop-blur-sm px-3 py-2 border border-border/50">
        <div className="text-xs text-muted-foreground uppercase tracking-wider">User Reports</div>
        <div className="text-xl font-semibold text-foreground">{inputs.length}</div>
      </div>

      {/* Selected input panel */}
      {selectedInput && (
        <div className="absolute bottom-4 right-4 z-[1000] w-80 rounded-lg bg-card/95 backdrop-blur-md border border-border p-4 shadow-2xl">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-white"></span>
              <span className="text-sm font-medium text-foreground">{selectedInput.author}</span>
            </div>
            <button
              onClick={() => setSelectedInput(null)}
              className="text-muted-foreground hover:text-foreground text-lg leading-none"
            >
              ×
            </button>
          </div>
          <p className="text-sm text-foreground/80 mb-2">{selectedInput.message}</p>
          <div className="text-xs text-muted-foreground">
            {new Date(selectedInput.timestamp).toLocaleString()}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {selectedInput.lat.toFixed(4)}°N, {Math.abs(selectedInput.lng).toFixed(4)}°{selectedInput.lng < 0 ? "W" : "E"}
          </div>
        </div>
      )}
    </div>
  );
}
