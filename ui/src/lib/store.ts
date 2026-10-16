import { create } from "zustand"
import { toast } from "sonner"
import { api } from "./api"
import { hidePanel, openFilePanel, showPanel } from "./dock"
import type { Conv, Settings, Task, Thread } from "./types"

type Panel = { open: boolean; tab: "checklist" | "tasks" | "file" | "browser" }

type State = {
  ready: boolean
  connected: boolean
  threads: Thread[]
  current: string | null
  convs: Record<string, Conv>
  status: Record<string, string>
  tps: Record<string, number>
  ctx: Record<string, { used: number; max: number; compact: number }>
  sys: { gpu?: number; vram_used?: number; vram_total?: number; cpu?: number; ram_used?: number; ram_total?: number; temp?: number | null; power?: number | null; error?: string }
  settings: Settings | null
  models: string[]
  openai: boolean
  openaiModels: string[]
  tasks: Task[]
  panel: Panel
  files: string[]
  activeFile: string | null
  settingsOpen: boolean
  sidebarOpen: boolean
  expanded: Record<string, boolean>

  init: () => Promise<void>
  select: (id: string | null) => Promise<void>
  loadConv: (id: string) => Promise<void>
  refreshTasks: () => Promise<void>
  openFile: (path: string) => void
  closeFile: (path: string) => void
  setPanel: (p: Partial<Panel>) => void
  toggle: (key: string) => void
  saveSettings: (patch: Partial<Settings>) => Promise<void>
  set: (p: Partial<State>) => void
}

let socket: WebSocket | null = null
let tasksTimer: number | null = null

export const useApp = create<State>((set, get) => ({
  ready: false,
  connected: false,
  threads: [],
  current: null,
  convs: {},
  status: {},
  tps: {},
  ctx: {},
  sys: {},
  settings: null,
  models: [],
  openaiModels: [],
  openai: false,
  tasks: [],
  panel: { open: false, tab: "checklist" },
  files: [],
  activeFile: null,
  settingsOpen: false,
  sidebarOpen: true,
  expanded: {},

  set: (p) => set(p),

  init: async () => {
    const replay = new URLSearchParams(location.search).get("replay")
    if (replay) {                      // dev: feed a recorded event stream instead of the backend (lib/replay.ts)
      ;(await import("./replay")).startReplay(replay)
      return
    }
    const s = await api.state()
    set({ settings: s.settings, threads: s.threads, models: s.models, openai: s.openai,
          openaiModels: (s as any).openai_models || [], ready: true })
    document.documentElement.dataset.zenReady = "1"     // the desktop window shows itself (and drops its splash) on this
    connect()
  },

  select: async (id) => {
    set({ current: id })
    import("./dock").then((d) => { if (!d.isVisible("chat")) d.showPanel("chat") })
    if (id) await get().loadConv(id)
  },

  loadConv: async (id) => {
    const c = await api.conv(id)
    set((st) => ({ convs: { ...st.convs, [id]: c }, status: { ...st.status, [id]: c.status || "" } }))
  },

  refreshTasks: async () => {
    try {
      const t = await api.tasks()
      set({ tasks: t.tasks })
    } catch {
      /* server restarting */
    }
  },

  openFile: (path) => openFilePanel(path),

  closeFile: (path) => hidePanel(`file:${path}`),

  setPanel: (p) => {
    if (p.tab && p.tab !== "file") showPanel(p.tab)
    else if (p.open === false && get().panel.tab !== "file") hidePanel(get().panel.tab)
    set((st) => ({ panel: { ...st.panel, ...p } }))
  },
  toggle: (key) => set((st) => ({ expanded: { ...st.expanded, [key]: !st.expanded[key] } })),

  saveSettings: async (patch) => {
    const r = await api.settings(patch)
    set({ settings: r.settings })
  },
}))

function scheduleTasks() {
  if (tasksTimer) return
  tasksTimer = window.setTimeout(() => {
    tasksTimer = null
    useApp.getState().refreshTasks()
  }, 250)
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws"
  socket = new WebSocket(`${proto}://${location.host}/ws`)
  socket.onopen = () => {
    const st = useApp.getState()
    useApp.setState({ connected: true })
    if (st.current) st.loadConv(st.current)
    st.refreshTasks()
  }
  socket.onclose = () => {
    useApp.setState({ connected: false })
    setTimeout(async () => {
      try {
        const s = await api.state()
        useApp.setState({ threads: s.threads, settings: s.settings })
      } catch {
        /* still down */
      }
      connect()
    }, 1500)
  }
  socket.onmessage = (ev) => applyEvent(JSON.parse(ev.data))
}

/** Apply one backend event (a WebSocket message) to the store. The replay player feeds recorded ones here too. */
export function applyEvent(m: any) {
  const st = useApp.getState()
  switch (m.t) {
    case "msgs": {
      const c = st.convs[m.conv]
      if (!c) return
      const messages = c.messages.slice(0, m.len)
      for (const [i, msg] of Object.entries(m.upd)) messages[Number(i)] = msg as any
      useApp.setState({ convs: { ...st.convs, [m.conv]: { ...c, messages } } })
      break
    }
    case "threads": {
      const threads: Thread[] = m.threads
      const convs = { ...st.convs }
      for (const t of threads) if (convs[t.id]) convs[t.id] = { ...convs[t.id], running: t.running, title: t.title }
      useApp.setState({ threads, convs })
      break
    }
    case "status":
      useApp.setState({ status: { ...st.status, [m.conv]: m.text } })
      break
    case "sys":
      useApp.setState({ sys: m })
      break
    case "tps":
      useApp.setState({ tps: { ...st.tps, [m.conv]: m.tps } })
      break
    case "workers": {                // fan-out workers' live state (tiles above the composer)
      const c = st.convs[m.conv]
      if (c) useApp.setState({ convs: { ...st.convs, [m.conv]: { ...c, workers: { model: m.model, items: m.items, ts: m.ts } } } })
      break
    }
    case "schedules":
      useApp.setState({ schedules: m.items } as any)
      break
    case "ctx":
      useApp.setState({ ctx: { ...st.ctx, [m.conv]: { used: m.used, max: m.max, compact: m.compact } } })
      break
    case "settings":
      useApp.setState({ settings: m.settings })
      break
    case "tasks":
      scheduleTasks()
      break
    case "run_end":
      useApp.setState({ status: { ...st.status, [m.conv]: "" } })
      scheduleTasks()
      break
    case "approval":
      if (m.conv !== st.current)
        toast.warning(`“${m.title}” needs your approval`, {
          description: m.desc,
          action: { label: "Open", onClick: () => useApp.getState().select(m.conv) },
          duration: 12000,
        })
      scheduleTasks()
      break
  }
}
