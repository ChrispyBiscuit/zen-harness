// Layout controller for the dockable workspace (dockview). The chat fills the middle; the side panels live in a
// right-hand tray (a dockview edge group) that collapses to a thin rail of tabs, like the left sidebar; DevTools
// opens in a bottom tray. Panels can still be dragged, split, floated and maximised; the layout (including the
// trays' size and collapsed state) is saved in localStorage, and a closed panel reopens where it was.
import type { DockviewApi, DockviewGroupPanel, IDockviewPanel } from "dockview-react"
import { basename } from "./paths"

export type PanelId = "chat" | "checklist" | "browser" | "tasks" | "artifacts" | "devtools"
export const PANEL_TITLES: Record<PanelId, string> = { chat: "Chat", checklist: "Checklist", browser: "Browser", tasks: "Tasks", artifacts: "Artifacts", devtools: "DevTools" }
// v2: side panels moved from a grid column into the right tray; older saved layouts are not restored
const LAYOUT_KEY = "lws.layout.v2"
const HOMES_KEY = "lws.panelHomes.v1"
const TRAY = "tray", CONSOLE = "console"
const TRAY_SIZE = 440, TRAY_MIN = 300, TRAY_MAX = 1100
const CHAT_MIN = 420

let api: DockviewApi | null = null
let saveTimer: number | null = null
const listeners = new Set<() => void>()

export const dockApi = () => api
export function onDockChange(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
const emit = () => listeners.forEach((f) => f())

/** Save the layout soon (folding a tray does not fire dockview's layout-change event, so trays call this). */
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    try {
      if (api) localStorage.setItem(LAYOUT_KEY, JSON.stringify(api.toJSON()))
    } catch { /* storage blocked or layout not serializable */ }
  }, 400)
}

// ------------------------------------------------------------------ trays (edge groups)

/** The right tray (created on demand). */
function tray(): DockviewGroupPanel | undefined {
  if (!api) return undefined
  if (!api.getEdgeGroup("right"))
    api.addEdgeGroup("right", { id: TRAY, initialSize: TRAY_SIZE, minimumSize: TRAY_MIN, maximumSize: TRAY_MAX, autoHide: true })
  const g = api.groups.find((g) => g.api.location.type === "edge" && g.id === TRAY) ??
    api.groups.find((g) => g.api.location.type === "edge" && (g.api as any).position === "right")
  tabsOnInside(g)
  return g
}

/** The bottom tray for DevTools (created on demand). */
function consoleTray(): DockviewGroupPanel | undefined {
  if (!api) return undefined
  if (!api.getEdgeGroup("bottom"))
    api.addEdgeGroup("bottom", { id: CONSOLE, initialSize: 280, minimumSize: 160, maximumSize: 700, autoHide: true })
  return api.groups.find((g) => g.api.location.type === "edge" && g.id === CONSOLE)
}

const trayApi = () => api?.getEdgeGroup("right")

/** The tray's tabs (and its fold button, at their foot) run down its left edge, facing the chat: the toggle sits
 *  at the tray's bottom-left, and when the tray folds that strip is the rail. */
function tabsOnInside(g: DockviewGroupPanel | undefined) {
  try { if (g && g.api.getHeaderPosition() !== "left") g.api.setHeaderPosition("left") } catch { /* older dockview */ }
}
/** The right tray if it exists (never creates it: safe to call while rendering). */
const findTray = () => api?.groups.find((g) => g.api.location.type === "edge" && g.id === TRAY)

/** Is the right tray collapsed to its rail (or absent, or empty)? */
export function trayCollapsed() {
  const g = trayApi()
  return !g || g.isCollapsed() || !findTray()?.panels.length
}

/** Tray panels shown as a drawer (floating) because the window was too narrow to dock them: their home stays the tray. */
const drawers = new Set<string>()

/** Width the tray may take beside a chat of CHAT_MIN (the dock shell's width less the chat's). */
function trayRoom() {
  const shell = (api?.groups[0]?.element.closest(".dv-shell") as HTMLElement | null)?.clientWidth
  return shell ? shell - CHAT_MIN - 12 : Infinity
}

export function expandTray() {
  if (!api || !trayApi()) return
  if (trayRoom() < TRAY_MIN) {           // a narrow window: open the panel as a drawer over the chat instead of docking
    const p = findTray()?.activePanel
    if (p) {
      drawers.add(p.id)
      const H = window.innerHeight, width = Math.min(TRAY_SIZE, window.innerWidth - 60)
      api.addFloatingGroup(p, onScreen({ width, height: H - 90, x: window.innerWidth - width - 12, y: 48 }) as any)
      emit()
      return
    }
  }
  api.pinEdgeGroup("right")
  trayApi()?.expand()
  emit()
  scheduleSave()
}

export function collapseTray() {
  if (!api || !trayApi()) return
  api.autoHideEdgeGroup("right")
  trayApi()?.collapse()
  emit()
  scheduleSave()
}

/** Collapse or expand the right tray (Ctrl+Alt+B / the header button). Expanding an empty tray opens the
 *  checklist in it. */
export function toggleTray() {
  if (!api) return
  if (!findTray()?.panels.length) { showPanel("checklist"); return }
  if (trayCollapsed()) expandTray()
  else collapseTray()
}

// ------------------------------------------------------------------ where a panel lives

type Home = { kind: "tray" | "console" | "grid" } | { kind: "float"; x: number; y: number; width: number; height: number }
let homes: Record<string, Home> = {}
try { homes = JSON.parse(localStorage.getItem(HOMES_KEY) || "{}") } catch { homes = {} }
function saveHomes() { try { localStorage.setItem(HOMES_KEY, JSON.stringify(homes)) } catch { /* storage blocked */ } }

/** Remember where a panel is, so it reopens there after being closed. */
function rememberHome(p: IDockviewPanel) {
  const g = p.group, loc = g.api.location.type
  if (loc === "edge") homes[p.id] = { kind: g.id === CONSOLE ? "console" : "tray" }
  else if (loc === "floating" && drawers.has(p.id)) { drawers.delete(p.id); homes[p.id] = { kind: "tray" } }
  else if (loc === "floating") {
    const box = (g.element.closest(".dv-resize-container") as HTMLElement | null)?.getBoundingClientRect()
    homes[p.id] = box ? { kind: "float", x: box.left, y: box.top, width: box.width, height: box.height } : { kind: "tray" }
  } else if (p.id !== "chat") homes[p.id] = { kind: "grid" }
  saveHomes()
}

/** A floating window's box, kept on screen. */
function onScreen(b: { x: number; y: number; width: number; height: number }) {
  const W = window.innerWidth, H = window.innerHeight
  const width = Math.min(b.width, W - 40), height = Math.min(b.height, H - 80)
  return { width, height, x: Math.max(10, Math.min(b.x, W - width - 10)), y: Math.max(40, Math.min(b.y, H - height - 10)) }
}

export function showPanel(id: PanelId) {
  if (!api) return
  const existing = api.getPanel(id)
  if (existing) {
    const folded = existing.group.api.location.type === "edge" && existing.group.id === TRAY && trayCollapsed()
    existing.api.setActive()                // first, so a narrow window's drawer opens the panel that was asked for
    if (folded) expandTray()
    if (existing.group.api.location.type === "edge" && existing.group.id === CONSOLE) api.pinEdgeGroup("bottom")
    return
  }
  if (id === "chat") { api.addPanel({ id, component: id, title: PANEL_TITLES[id] }); return }
  const home: Home = homes[id] ?? { kind: id === "devtools" ? "console" : "tray" }
  if (home.kind === "float") {
    api.addPanel({ id, component: id, title: PANEL_TITLES[id], floating: onScreen(home) as any })
  } else if (home.kind === "grid" && api.getPanel("chat")) {
    api.addPanel({ id, component: id, title: PANEL_TITLES[id], position: { referencePanel: "chat", direction: "right" },
      initialWidth: id === "artifacts" ? 560 : TRAY_SIZE })
  } else if (home.kind === "console") {
    const g = consoleTray()
    api.addPanel({ id, component: id, title: PANEL_TITLES[id], position: g ? { referenceGroup: g } : undefined })
    api.pinEdgeGroup("bottom")
  } else {
    const g = tray()
    api.addPanel({ id, component: id, title: PANEL_TITLES[id], position: g ? { referenceGroup: g } : undefined })
    expandTray()
  }
}

/** Is the panel open and actually showing (its tab active, and its tray not collapsed)? */
export function isVisible(id: string) {
  const p = api?.getPanel(id)
  if (!p || !p.api.isVisible) return false
  if (p.group.api.location.type === "edge") {
    const e = api!.getEdgeGroup(p.group.id === CONSOLE ? "bottom" : "right")
    return !!e && !e.isCollapsed()
  }
  return true
}

const REDUCED = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
const closing = new Set<string>()

/** Close a panel with an exit: if its group (or floating window) goes with it, that sinks and fades first;
 *  a tab among others just closes (the next tab's content fades in, index.css .zen-panel-in). */
export function closeAnimated(panel: IDockviewPanel) {
  if (closing.has(panel.id)) return
  rememberHome(panel)
  const group = panel.group
  const floating = group.api.location.type === "floating"
  const edge = group.api.location.type === "edge"
  const el = group.panels.length !== 1 || edge ? null
    : floating ? (group.element.closest(".dv-resize-container") as HTMLElement | null) : group.element
  if (!el || REDUCED || !el.animate) { panel.api.close(); return }
  closing.add(panel.id)
  const anim = el.animate(
    [{ opacity: 1, transform: "none" },
     { opacity: 0, transform: floating ? "translateY(10px) scale(0.96)" : "translateY(4px) scale(0.985)" }],
    { duration: floating ? 170 : 150, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)", fill: "forwards" })
  const done = () => { closing.delete(panel.id); panel.api.close() }
  anim.onfinish = done
  anim.oncancel = done
}

export function hidePanel(id: string) {
  const p = api?.getPanel(id)
  if (p && id !== "chat") closeAnimated(p)
}

/** The header's panel buttons: show the panel; if it is already showing in the tray, fold the tray away (the
 *  panel stays in it). Panels elsewhere (split, floating) close as before. */
export function togglePanel(id: PanelId) {
  const p = api?.getPanel(id)
  if (!p || !isVisible(id) || !p.api.isActive) { showPanel(id); return }
  if (p.group.api.location.type === "edge" && p.group.id === TRAY) collapseTray()
  else hidePanel(id)
}

export function openFilePanel(path: string) {
  if (!api) return
  const id = `file:${path}`
  const existing = api.getPanel(id)
  if (existing) {
    if (existing.group.id === TRAY && trayCollapsed()) expandTray()
    existing.api.setActive()
    return
  }
  const g = tray()
  api.addPanel({ id, component: "file", title: basename(path), params: { path }, position: g ? { referenceGroup: g } : undefined })
  expandTray()
}

export function floatPanel(id: string) {
  const p = api?.getPanel(id)
  if (p && api) api.addFloatingGroup(p, onScreen({ width: 640, height: 480, x: 120, y: 80 }) as any)
}

/** Double-click on a group's tab bar: maximise / restore a grid group; fold away a tray. */
export function toggleGroupMaximized(g: DockviewGroupPanel) {
  if (!api) return
  const loc = g.api.location.type
  if (loc === "edge") { if (g.id === TRAY) collapseTray(); else api.autoHideEdgeGroup("bottom"); return }
  if (loc !== "grid") return
  if (g.api.isMaximized()) g.api.exitMaximized()
  else g.api.maximize()
}

/** The chat's own tab bar ("Chat ×", float, maximise) says nothing the top bar doesn't: hide it while the chat is
 *  alone in its group; it comes back if another panel is docked in with it (so you can switch between them). */
function syncChatHeader() {
  const g = api?.getPanel("chat")?.group
  if (!g) return
  const alone = g.panels.length === 1 && g.api.location.type === "grid"
  try { const h = (g as any).header ?? (g as any).model?.header; if (h && h.hidden !== alone) h.hidden = alone } catch { /* older dockview */ }
}

/** Keep the chat from being crushed by a wide tray or a split. */
function constrainChat() {
  const g = api?.getPanel("chat")?.group
  if (g && g.api.location.type === "grid") {
    try { (g.api as any).setConstraints?.({ minimumWidth: CHAT_MIN, minimumHeight: 240 }) } catch { /* older dockview */ }
  }
}

/** The tray is an edge group beside the grid, so the chat's minimum width doesn't hold it back: in a narrow window
 *  an expanded tray kept its full width and squeezed the chat to a sliver. Shrink the tray so the chat keeps
 *  CHAT_MIN (never below TRAY_MIN); if even that doesn't fit, fold the tray to its rail. */
let fitting = false
function fitTray() {
  if (!api || fitting) return
  const t = trayApi()
  if (!t || t.isCollapsed()) return
  const width = findTray()!.element.getBoundingClientRect().width
  const room = trayRoom()
  if (width <= room + 1) return
  fitting = true
  try {
    if (room >= TRAY_MIN) t.setSize({ width: room })
    else collapseTray()
  } finally { fitting = false }
}
let fitQueued = 0
const queueFitTray = () => { cancelAnimationFrame(fitQueued); fitQueued = requestAnimationFrame(fitTray) }

function defaultLayout() {
  if (!api) return
  api.clear()
  for (const pos of ["right", "bottom", "left", "top"] as const) if (api.getEdgeGroup(pos)) api.removeEdgeGroup(pos)
  api.addPanel({ id: "chat", component: "chat", title: "Chat" })
  const g = tray()
  for (const [id, inactive] of [["checklist", false], ["browser", true], ["tasks", true], ["artifacts", true]] as const)
    api.addPanel({ id, component: id, title: PANEL_TITLES[id], position: g ? { referenceGroup: g } : undefined, inactive })
  api.getPanel("checklist")?.api.setActive()
  api.getPanel("chat")?.api.setActive()
  constrainChat()
}

export function resetLayout() {
  try {
    localStorage.removeItem(LAYOUT_KEY)
    localStorage.removeItem(HOMES_KEY)
  } catch { /* storage blocked */ }
  homes = {}
  defaultLayout()
  expandTray()
  emit()
  scheduleSave()
}

/** Floating windows stay reachable when the window shrinks. */
function keepFloatsOnScreen() {
  if (!api) return
  for (const g of api.groups) {
    if (g.api.location.type !== "floating") continue
    const box = (g.element.closest(".dv-resize-container") as HTMLElement | null)
    const r = box?.getBoundingClientRect()
    if (!box || !r) continue
    const fit = onScreen({ x: r.left, y: r.top, width: r.width, height: r.height })
    if (Math.abs(fit.x - r.left) > 1 || Math.abs(fit.y - r.top) > 1) {
      try { (g.api as any).setBounds?.({ left: fit.x, top: fit.y }) } catch { /* not supported: leave it */ }
    }
  }
}

export function initDock(a: DockviewApi) {
  api = a
  let restored = false
  try {
    const saved = localStorage.getItem(LAYOUT_KEY)
    if (saved) {
      api.fromJSON(JSON.parse(saved))
      restored = !!api.getPanel("chat")
    }
  } catch {
    restored = false
  }
  if (!restored) defaultLayout()
  constrainChat()
  tabsOnInside(findTray())
  syncChatHeader()
  api.onDidLayoutChange(() => { syncChatHeader(); emit(); scheduleSave(); queueFitTray() })
  api.onDidAddPanel(() => setTimeout(syncChatHeader, 0))
  api.onDidRemovePanel(() => setTimeout(syncChatHeader, 0))
  scheduleSave()
  api.onDidActivePanelChange(() => emit())
  // remember where panels are as they move, so a closed one reopens in the same place
  api.onDidRemovePanel((p) => { try { if (p.id !== "chat" && !homes[p.id]) rememberHome(p) } catch { /* group gone */ } })
  api.onDidMovePanel?.((e: any) => { try { if (e?.panel) rememberHome(e.panel) } catch { /* mid-move */ } })
  window.addEventListener("resize", () => { keepFloatsOnScreen(); queueFitTray() })
  queueFitTray()
  // the desktop window asks for panels too (F12 in the agent's browser opens DevTools)
  ;(window as any).__zenShowPanel = (id: PanelId) => showPanel(id)
  ;(window as any).__zenHidePanel = (id: string) => hidePanel(id)
  // the chat is the app itself: if it gets closed, put it straight back
  api.onDidRemovePanel((p) => { if (p.id === "chat") setTimeout(() => { showPanel("chat"); constrainChat() }, 0) })
  emit()
}
