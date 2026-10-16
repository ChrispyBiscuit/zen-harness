import { useEffect, useRef, useState } from "react"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { MapPin } from "lucide-react"
import { Frame } from "./Frame"
import { num, str } from "./parse"

/* ```map: places on an interactive map (Leaflet, OpenStreetMap tiles darkened to the app). Loaded only when an
   answer has a map. Markers without coordinates are geocoded by the backend (/api/geocode: cached Nominatim, refused
   when web access is off). The places are always listed under the map too, so it reads offline or without tiles. */

type Place = { name: string; note: string; lat: number | null; lon: number | null }

function readPlaces(d: any): Place[] {
  const rows: any[] = Array.isArray(d) ? d : Array.isArray(d?.markers) ? d.markers : Array.isArray(d?.places) ? d.places
    : Array.isArray(d?.points) ? d.points : []
  return rows.slice(0, 25).map((r) => typeof r === "string" ? { name: r, note: "", lat: null, lon: null } : {
    name: str(r.name ?? r.title ?? r.label ?? r.place),
    note: str(r.note ?? r.text ?? r.description ?? ""),
    lat: num(r.lat ?? r.latitude ?? r.coords?.[0]),
    lon: num(r.lon ?? r.lng ?? r.long ?? r.longitude ?? r.coords?.[1]),
  }).filter((p) => p.name || (p.lat != null && p.lon != null))
}

const valid = (p: Place) => p.lat != null && p.lon != null && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180

export default function MapBlock({ d }: { d: any }) {
  const [places, setPlaces] = useState<Place[]>(() => readPlaces(d))
  const [status, setStatus] = useState<"" | "locating" | "offline" | "none">("")
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const [sel, setSel] = useState<number | null>(null)

  useEffect(() => {                                          // fill in coordinates for places named only
    const missing = places.filter((p) => !valid(p) && p.name).map((p) => p.name)
    if (!missing.length) return
    let off = false
    setStatus("locating")
    fetch("/api/geocode", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ names: missing }) })
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (off) return
        const got = j?.places || {}
        setPlaces((ps) => ps.map((p) => (valid(p) || !got[p.name] ? p : { ...p, lat: got[p.name].lat, lon: got[p.name].lon })))
        setStatus(ok ? "" : "offline")
      })
      .catch(() => { if (!off) setStatus("offline") })
    return () => { off = true }
  }, [])                                                      // eslint-disable-line react-hooks/exhaustive-deps

  const located = places.filter(valid)

  useEffect(() => {
    if (!el.current || map.current) return
    const m = L.map(el.current, { zoomControl: true, scrollWheelZoom: false, attributionControl: true, worldCopyJump: true })
    // OpenStreetMap's own tiles (no key; light use by its tile policy), darkened in CSS (.zen-map .leaflet-tile-pane)
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors',
    }).addTo(m)
    m.setView([20, 0], 1)
    m.on("click", () => m.scrollWheelZoom.enable())          // the wheel scrolls the chat until the map is clicked
    m.on("mouseout", () => m.scrollWheelZoom.disable())
    layer.current = L.layerGroup().addTo(m)
    map.current = m
    return () => { m.remove(); map.current = null }
  }, [])

  useEffect(() => {
    const m = map.current, g = layer.current
    if (!m || !g) return
    g.clearLayers()
    const pts: L.LatLngExpression[] = []
    places.forEach((p, i) => {
      if (!valid(p)) return
      const ll: L.LatLngExpression = [p.lat!, p.lon!]
      pts.push(ll)
      const icon = L.divIcon({ className: "zen-pin", html: `<span>${i + 1}</span>`, iconSize: [22, 22], iconAnchor: [11, 11] })
      const mk = L.marker(ll, { icon, title: p.name, riseOnHover: true }).addTo(g)
      const tip = document.createElement("div")
      const b = document.createElement("div"); b.className = "zen-pin-name"; b.textContent = p.name; tip.appendChild(b)
      if (p.note) { const n = document.createElement("div"); n.className = "zen-pin-note"; n.textContent = p.note; tip.appendChild(n) }
      mk.bindTooltip(tip, { direction: "top", offset: [0, -10], className: "zen-pin-tip" })
      mk.on("click", () => setSel(i))
    })
    if (pts.length === 1) m.setView(pts[0], 13)
    else if (pts.length) m.fitBounds(L.latLngBounds(pts), { padding: [28, 28], maxZoom: 14 })
    setTimeout(() => m.invalidateSize(), 0)
  }, [places])

  const focus = (i: number) => {
    const p = places[i]
    setSel(i)
    if (valid(p) && map.current) map.current.flyTo([p.lat!, p.lon!], Math.max(map.current.getZoom(), 13), { duration: 0.6 })
  }

  const title = Array.isArray(d) ? "" : str(d?.title)
  const showMap = located.length > 0 || status === "locating"
  return (
    <Frame title={title || undefined} bodyClass="px-0 pb-0 pt-0" className={title ? "" : "pt-0"}>
      <div ref={el} className={"zen-map h-[300px] w-full bg-surface-2 " + (showMap ? "" : "hidden")} />
      {status === "locating" && !located.length && <div className="px-3.5 pt-2 text-meta text-faint">Finding the places…</div>}
      {status === "offline" && !located.length && <div className="px-3.5 pt-2 text-meta text-faint">The map needs web access to place these; here they are as a list.</div>}
      <ol className="grid gap-x-4 gap-y-0.5 px-2 py-2 text-ui [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
        {places.map((p, i) => (
          <li key={i}>
            <button onClick={() => focus(i)} disabled={!valid(p)}
              className={"flex w-full min-w-0 items-start gap-2 rounded-[var(--radius-chip)] px-1.5 py-1 text-left transition-colors enabled:hover:bg-surface-2 " + (sel === i ? "bg-surface-2" : "")}>
              <span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full bg-[var(--series-1)] text-[9.5px] font-semibold text-white">{i + 1}</span>
              <span className="min-w-0">
                <span className="block truncate text-foreground">{p.name}</span>
                {p.note && <span className="block truncate text-meta text-faint">{p.note}</span>}
              </span>
              {!valid(p) && status !== "locating" && <MapPin className="ml-auto mt-0.5 size-3 shrink-0 text-faint opacity-50" aria-label="not on the map" />}
            </button>
          </li>
        ))}
      </ol>
    </Frame>
  )
}
