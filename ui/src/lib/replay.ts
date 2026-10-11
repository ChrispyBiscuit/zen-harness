/** Dev-only event replay: `?replay=<name>[&speed=4]` loads replays/<name>.json (built by scripts/make_replays.py)
 *  and feeds its recorded backend events into the store at real or faster speed, so the UI can be developed and
 *  measured with no backend and no model running. Every /api request is answered locally; nothing leaves the page.
 *  A small HUD (bottom-left) pauses, restarts and changes speed; `window.__replay` does the same from scripts. */
import * as dock from "./dock"
import { applyEvent, useApp } from "./store"
import type { Conv, Thread } from "./types"

type ReplayFile = {
  version: number
  title: string
  state: { settings: any; models: string[]; threads: Thread[]; openai?: boolean; openai_models?: string[] }
  convs: Conv[]
  select?: string
  events: { at: number; ev: any }[]
}

let file: ReplayFile
let idx = 0
let clock = 0                 // replay time (ms at 1x) reached so far
let last = 0                  // wall time of the previous tick
let speed = 1
let playing = true
let timer = 0
let hud: HTMLDivElement | null = null
let runStarted: number | null = null
let doneResolve: (() => void) | null = null

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

/** Answer the app's /api calls from the replay; nothing reaches a server. */
function patchFetch() {
  const real = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
    const path = url.replace(location.origin, "")
    if (!path.startsWith("/api")) return real(input, init)
    if (path.startsWith("/api/file?raw=1")) return real(input, init)     // replay images: served by the dev plugin
    const method = (init?.method || "GET").toUpperCase()
    const conv = /^\/api\/conv\/([^/?]+)$/.exec(path)
    if (method === "GET" && conv) {
      const c = useApp.getState().convs[conv[1]] || file.convs.find((x) => x.id === conv[1])
      return c ? json(c) : json({ error: "not in this replay" }, 404)
    }
    if (method === "GET") {
      if (path.startsWith("/api/state")) return json({ ...file.state, user: "demo" })
      if (path.startsWith("/api/tasks")) return json({ tasks: [] })
      if (path.startsWith("/api/schedules")) return json({ items: [] })
      if (path.startsWith("/api/playbooks")) return json({ books: [], notes: [], matches: [] })
    }
    return json({ error: "replay mode: no backend" }, 503)
  }
}

function threadsWith(running: boolean): Thread[] {
  const id = file.select
  const now = Date.now() / 1000
  if (running) runStarted = now
  return useApp.getState().threads.map((t) => (t.id === id
    ? { ...t, running, run_started: runStarted, run_ended: running ? null : now } : t))
}

function feed(ev: any) {
  if (ev.t === "threads" && typeof ev.threads === "string") {          // placeholders: mark the run live / ended
    applyEvent({ t: "threads", threads: threadsWith(ev.threads === "__running__") })
  } else if (ev.t === "_delta") {                                      // compact text delta -> a real msgs event
    const c = useApp.getState().convs[ev.conv]
    const m = c?.messages[ev.i]
    if (!c || !m) return
    const upd = { ...m, [ev.field]: ((m as any)[ev.field] || "") + ev.add }
    applyEvent({ t: "msgs", conv: ev.conv, len: c.messages.length, upd: { [ev.i]: upd } })
  } else applyEvent(ev)
}

function tick() {
  const now = performance.now()
  if (playing) clock += (now - last) * speed
  last = now
  while (idx < file.events.length && file.events[idx].at <= clock) feed(file.events[idx++].ev)
  renderHud()
  if (idx >= file.events.length) {
    playing = false
    renderHud()
    doneResolve?.()
    return
  }
  // sleep until the next event is due (capped, so the HUD clock keeps moving)
  const wait = playing ? Math.min(250, Math.max(8, (file.events[idx].at - clock) / speed)) : 250
  timer = window.setTimeout(tick, wait)
}

function reset() {
  window.clearTimeout(timer)
  idx = 0
  clock = 0
  runStarted = null
  const convs: Record<string, Conv> = {}
  for (const c of file.convs) convs[c.id] = structuredClone(c)
  useApp.setState({ convs, threads: structuredClone(file.state.threads), status: {}, tps: {}, ctx: {} })
}

function play() {
  window.clearTimeout(timer)
  playing = true
  last = performance.now()
  timer = window.setTimeout(tick, 0)
}

const fmt = (ms: number) => {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

function renderHud() {
  if (!hud) return
  const total = file.events.length ? file.events[file.events.length - 1].at : 0
  const status = hud.querySelector<HTMLSpanElement>("[data-s]")!
  status.textContent = total ? `${fmt(Math.min(clock, total))} / ${fmt(total)}` : "static"
  hud.querySelector<HTMLButtonElement>("[data-play]")!.textContent = playing ? "pause" : "play"
  hud.querySelectorAll<HTMLButtonElement>("[data-speed]").forEach((b) => {
    b.style.opacity = Number(b.dataset.speed) === speed ? "1" : "0.45"
  })
}

function mountHud(name: string) {
  if (new URLSearchParams(location.search).get("hud") === "0") return    // clean screenshots
  hud = document.createElement("div")
  hud.setAttribute("role", "toolbar")
  hud.setAttribute("aria-label", "Replay controls")
  hud.style.cssText = "position:fixed;left:8px;bottom:8px;z-index:9999;display:flex;gap:8px;align-items:center;" +
    "padding:4px 8px;border-radius:6px;background:oklch(0.2 0.005 265 / 92%);border:1px solid oklch(1 0 0 / 10%);" +
    "font:500 10.5px var(--font-mono);letter-spacing:.08em;text-transform:uppercase;color:oklch(0.8 0.01 265)"
  const btn = "background:none;border:0;color:inherit;font:inherit;letter-spacing:inherit;text-transform:inherit;" +
    "cursor:pointer;padding:2px 4px"
  hud.innerHTML = `<span style="color:oklch(0.8 0.14 75)">replay</span><span>${name.replace(/[^\w-]/g, "")}</span>` +
    `<span data-s></span><button data-play style="${btn}"></button><button data-restart style="${btn}">restart</button>` +
    [1, 4, 16].map((s) => `<button data-speed="${s}" style="${btn}">${s}x</button>`).join("")
  hud.querySelector("[data-play]")!.addEventListener("click", () => (playing ? (playing = false, renderHud()) : play()))
  hud.querySelector("[data-restart]")!.addEventListener("click", () => { reset(); play() })
  hud.querySelectorAll<HTMLButtonElement>("[data-speed]").forEach((b) =>
    b.addEventListener("click", () => { speed = Number(b.dataset.speed); renderHud() }))
  document.body.appendChild(hud)
}

export async function startReplay(name: string) {
  const safe = name.replace(/[^\w-]/g, "")
  const params = new URLSearchParams(location.search)
  speed = Math.max(0.25, Math.min(64, Number(params.get("speed")) || 1))
  patchFetch()
  const r = await fetch(`./replays/${safe}.json`, { cache: "no-store" })
  if (!r.ok) {
    useApp.setState({ ready: true })
    document.body.insertAdjacentHTML("afterbegin",
      `<div style="padding:12px;color:#f87171;font:13px monospace">replay "${safe}" not found (run scripts/make_replays.py)</div>`)
    return
  }
  file = await r.json()
  const s = file.state
  useApp.setState({ settings: s.settings, models: s.models, openai: !!s.openai, openaiModels: s.openai_models || [],
                    ready: true, connected: true })
  reset()
  const pick = params.get("conv") ?? file.select                      // &conv=<id> or &conv=none (empty state)
  if (pick && pick !== "none") await useApp.getState().select(file.convs.find((c) => c.id.startsWith(pick))?.id || pick)
  ;(window as any).__app = useApp                                       // for scripts/shoot.mjs (dialogs, panels)
  ;(window as any).__dock = dock
  mountHud(safe)
  const done = new Promise<void>((res) => { doneResolve = res })
  ;(window as any).__replay = {
    title: file.title, events: file.events.length, done,
    play, pause: () => { playing = false; renderHud() }, restart: () => { reset(); play() },
    setSpeed: (x: number) => { speed = x; renderHud() },
    state: () => ({ idx, clock, speed, playing }),
  }
  if (file.events.length && params.get("autoplay") !== "0") play()
  else { playing = false; renderHud() }
}
