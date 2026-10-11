import { useEffect, useRef, useState, useMemo } from "react"
import { BookOpen, CircleHelp, Cpu, Info, LayoutGrid, PanelLeftOpen, Plug, Scale, Settings2, WifiOff } from "lucide-react"
import { filesIn } from "@/components/ArtifactsPanel"
import { latestChecklist } from "@/components/Checklist"
import { Toaster } from "sonner"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Sidebar } from "@/components/Sidebar"
import { Workspace } from "@/components/Workspace"
import { isVisible, onDockChange, resetLayout, showPanel, toggleTray } from "@/lib/dock"
import { SettingsDialog } from "@/components/SettingsDialog"
import { HelpDialog } from "@/components/HelpDialog"
import { WelcomeDialog } from "@/components/WelcomeDialog"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Clip } from "@/components/Clip"
import { ResizeEdges, WindowControls, WindowFrame, useWindowDrag } from "@/components/WindowFrame"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"
import { MeterBar } from "@/components/MeterBar"
import { levelTone, type Tone } from "@/lib/tone"
import { applyDensity, storedDensity, type Density } from "@/lib/density"

function useTick(on: boolean) {
  const [, set] = useState(0)
  useEffect(() => {
    if (!on) return
    const t = setInterval(() => set((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [on])
}

function fmtElapsed(sec: number) {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`
}

function Meter({ label, pct, text, title }: { label: string; pct?: number; text: string; title?: string }) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap" title={title}>
      <span className="label-tech text-muted-foreground/60">{label}</span>
      {pct != null && <MeterBar pct={pct} tone={levelTone(pct)} label={label} className="w-10" />}
      <span className="mono tabular-nums text-foreground/80">{text}</span>
    </div>
  )
}

function fmtK(n: number) {
  return n >= 100_000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/** The context window the selected model will run with: a tuned profile's own value wins over the general setting. */
function configuredCtx(a: any): number {
  return Number(a?.model_overrides?.[a?.model]?.num_ctx || a?.num_ctx || 0)
}

/** How full the conversation's context window is, and when it will be compressed. */
function ContextGauge() {
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const live = useApp((s) => (s.current ? s.ctx[s.current] : undefined))
  const settings = useApp((s) => s.settings)
  const fallback = useMemo(() => {
    if (!conv) return undefined
    if ((conv as any).ctx?.max) return (conv as any).ctx as { used: number; max: number; compact: number }
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      const st = conv.messages[i].stats as any
      if (st?.context_max) return { used: st.context_used || 0, max: st.context_max, compact: Math.round(st.context_max * 0.75) }
    }
    return undefined
  }, [conv])
  const c = live || fallback
  if (!conv) return null
  const max = c?.max || configuredCtx(settings?.agent)
  const used = c?.used || 0
  const pct = max ? Math.min(100, (100 * used) / max) : 0
  const compactPct = max && c?.compact ? (100 * c.compact) / max : 75
  const tone: Tone = pct >= compactPct ? "destructive" : pct >= compactPct * 0.8 ? "warning" : "info"
  const left = Math.max(0, max - used)
  return (
    <span className="flex items-center"
      title={c ? `Context window: ${used.toLocaleString()} of ${max.toLocaleString()} tokens used (${pct.toFixed(0)}%), ` +
        `${left.toLocaleString()} remaining.\nOlder messages are compressed automatically at ${Math.round(compactPct)}% ` +
        `(${(c.compact || 0).toLocaleString()} tokens).` : "Context window: no data yet for this chat"}>
      <MeterBar pct={pct} tone={tone} marker={compactPct} label="Context used" className="h-1.5 w-20" />
    </span>
  )
}

function Monitor() {
  const sys = useApp((s) => s.sys)
  const thread = useApp((s) => s.threads.find((t) => t.id === s.current))
  const tps = useApp((s) => (s.current ? s.tps[s.current] : undefined))
  const running = !!thread?.running
  useTick(running)
  const started = thread?.run_started
  const elapsed = started ? ((running ? Date.now() / 1000 : thread?.run_ended || Date.now() / 1000) - started) : null
  const vramPct = sys.vram_total ? Math.round((100 * (sys.vram_used || 0)) / sys.vram_total) : undefined
  const ramPct = sys.ram_total ? Math.round((100 * (sys.ram_used || 0)) / sys.ram_total) : undefined
  if (sys.error && !started && ramPct == null && sys.cpu == null) return null
  return (
    <div className="flex items-center gap-3.5">
      {sys.cpu != null && <Meter label="CPU" pct={sys.cpu} text={`${sys.cpu}%`} title="Processor load, all cores" />}
      {ramPct != null && <Meter label="RAM" pct={ramPct} text={`${ramPct}%`}
        title={`System memory: ${((sys.ram_used || 0) / 1024).toFixed(1)} / ${((sys.ram_total || 0) / 1024).toFixed(1)} GB in use`} />}
      {!sys.error && <Meter label="GPU" pct={sys.gpu} text={`${sys.gpu ?? "–"}%`}
        title={`GPU utilization${sys.temp != null ? ` · ${sys.temp}°C` : ""}${sys.power != null ? ` · ${sys.power.toFixed(0)} W` : ""}`} />}
      {!sys.error && <Meter label="VRAM" pct={vramPct} text={`${vramPct ?? "–"}%`}
        title={`${((sys.vram_used || 0) / 1024).toFixed(1)} / ${((sys.vram_total || 0) / 1024).toFixed(1)} GB`} />}
      {running && <Meter label="tok/s" text={tps ? tps.toFixed(1) : "–"} title="Generation speed right now" />}
      {running && <Meter label="elapsed" text={elapsed != null ? fmtElapsed(elapsed) : "–"} title="Time the agent has spent on this request" />}
    </div>
  )
}

/** The bottom status bar (full width, under the sidebar and the workspace): on the left the sidebar's reopen button
 *  (while it is hidden), the model, its context size and this chat's context gauge; on the right the system gauges
 *  (CPU, RAM, GPU, VRAM) and, during a run, speed and elapsed time. Keeps the top bar to the title and actions. */
function StatusBar() {
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const settings = useApp((s) => s.settings)
  const sidebarOpen = useApp((s) => s.sidebarOpen)
  const connected = useApp((s) => s.connected)
  const a = settings?.agent
  const mode = conv?.mode || settings?.ui.mode
  const model = mode === "chat" ? settings?.ui.chat_model
    : mode === "speech" ? `Speech mode · ${(settings?.ui as any)?.speech_model || "qwen3:1.7b"}`
    : a?.model?.split(/[\\/]/).pop()?.replace(/\.gguf$/i, "")
  return (
    <footer className="@container flex h-7 shrink-0 select-none items-center gap-3 border-t border-line-1 bg-sidebar px-2 text-meta text-muted-foreground">
      {!sidebarOpen && (
        <button onClick={() => useApp.getState().set({ sidebarOpen: true })} title="Show sidebar (Ctrl+B)" aria-label="Show sidebar"
          className="grid size-5 place-items-center rounded text-muted-foreground transition-ui hover:bg-surface-2 hover:text-foreground"><PanelLeftOpen className="size-3.5" /></button>
      )}
      {!connected && <span className="flex items-center gap-1 text-destructive"><WifiOff className="size-3" /> reconnecting</span>}
      <span className="flex min-w-0 items-center gap-2" title="Model · context window · how full this chat's context is">
        <Cpu className="size-3.5 shrink-0 text-muted-foreground/70" />
        <span className="mono hidden min-w-0 truncate text-foreground/80 @xl:inline">{model}</span>
        {mode !== "chat" && a && <span className="mono shrink-0 text-muted-foreground/60">{Math.round(configuredCtx(a) / 1024)}k</span>}
        <ContextGauge />
      </span>
      <div className="flex-1" />
      <div className="hidden @2xl:flex"><Monitor /></div>
    </footer>
  )
}

function TopBar() {
  const current = useApp((s) => s.current)
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const thread = useApp((s) => s.threads.find((t) => t.id === s.current))
  const drag = useWindowDrag()
  const [, setTick] = useState(0)
  useEffect(() => { const off = onDockChange(() => setTick((x) => x + 1)); return () => { off() } }, [])
  const set = useApp((s) => s.set)
  const connected = useApp((s) => s.connected)
  return (
    // the top bar is also the window's title bar: empty space drags the window, the controls sit at the right end
    <header onPointerDown={drag} className={cn("@container flex h-12 shrink-0 select-none items-center gap-2 border-b border-border bg-surface-0 pl-3",
      drag ? "pr-0" : "pr-3")}>
      <div className="min-w-0 flex-1 truncate text-ui font-medium">{current ? (thread?.title || conv?.title || "") : "New chat"}</div>
      {!connected && <span className="flex items-center gap-1 text-caption text-destructive"><WifiOff className="size-3.5" /> reconnecting</span>}
      <Button variant="ghost" size="icon-sm" onClick={() => { if (confirm("Reset the panel layout to the default?")) resetLayout() }} title="Reset panel layout" aria-label="Reset panel layout"><LayoutGrid /></Button>
      <DropdownMenu>
        <DropdownMenuTrigger title="Help" aria-label="Help" className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"><CircleHelp className="size-4" /></DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem onClick={() => set({ helpOpen: true, helpPage: "getting-started" } as any)}><BookOpen /> Help</DropdownMenuItem>
          <DropdownMenuItem onClick={() => set({ helpOpen: true, helpPage: "connect-apps" } as any)}><Plug /> Connect other apps</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => set({ helpOpen: true, helpPage: "about" } as any)}><Info /> About Zen Harness</DropdownMenuItem>
          <DropdownMenuItem onClick={() => set({ helpOpen: true, helpPage: "licenses" } as any)}><Scale /> Licences</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button variant="ghost" size="icon-sm" onClick={() => set({ settingsOpen: true })} title="Settings" aria-label="Settings"><Settings2 /></Button>
      <WindowControls className="ml-1" />
    </header>
  )
}

export default function App() {
  const ready = useApp((s) => s.ready)
  const init = useApp((s) => s.init)
  const current = useApp((s) => s.current)
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const sidebarOpen = useApp((s) => s.sidebarOpen)
  // the sidebar slides in and out; "open"/"closed" are at rest, "opening"/"closing" animate (index.css)
  const [side, setSide] = useState<"open" | "opening" | "closing" | "closed">(sidebarOpen ? "open" : "closed")
  const firstSide = useRef(true)
  useEffect(() => {
    if (firstSide.current) { firstSide.current = false; return }
    setSide(sidebarOpen ? "opening" : "closing")
  }, [sidebarOpen])
  const panelOpen = useApp((s) => s.panel.open)
  const hasChecklist = useApp((s) => !!(s.current && s.convs[s.current] && latestChecklist(s.convs[s.current].messages)))
  const autoOpened = useRef(new Set<string>())
  const browsing = useApp((s) => {
    const c = s.current ? s.convs[s.current] : null
    const last = c?.messages[c.messages.length - 1]
    return !!(last?.kind === "tool" && last.tool?.name?.startsWith("browser_") && last.tool.result == null)
  })
  const browserOpened = useRef(new Set<string>())
  useEffect(() => {                 // the agent started browsing: show it once per thread
    if (current && browsing && !browserOpened.current.has(current)) {
      browserOpened.current.add(current)
      if (!isVisible("browser")) showPanel("browser")
    }
  }, [current, browsing])
  // the agent created or revised an artifact while you watch: bring the Artifacts pane forward and select it
  const lastArtifact = useApp((s) => {
    const c = s.current ? s.convs[s.current] : null
    if (!c) return ""
    for (let i = c.messages.length - 1; i >= Math.max(0, c.messages.length - 6); i--) {
      const d = c.messages[i].tool?.display as any
      if (d?.type === "artifact" && c.messages[i].tool?.result != null) return `${c.id}|${d.id}|${d.version}`
    }
    return ""
  })
  const seenArtifact = useRef<{ conv: string | null; key: string }>({ conv: null, key: "" })
  useEffect(() => {
    const prev = seenArtifact.current
    seenArtifact.current = { conv: current, key: lastArtifact }
    if (prev.conv !== current || !lastArtifact || lastArtifact === prev.key) return   // switching threads: no pop-up
    useApp.getState().set({ artifactSel: lastArtifact.split("|")[1] } as any)
    showPanel("artifacts")
  }, [current, lastArtifact])
  // the agent wrote its first file in this thread while you watch: show the Artifacts pane (once per thread)
  const fileCount = useApp((s) => (s.current && s.convs[s.current] ? filesIn(s.convs[s.current].messages).length : 0))
  const filesSeen = useRef<{ conv: string | null; n: number }>({ conv: null, n: 0 })
  const filesOpened = useRef(new Set<string>())
  useEffect(() => {
    const prev = filesSeen.current
    filesSeen.current = { conv: current, n: fileCount }
    if (!current || prev.conv !== current || fileCount <= prev.n || filesOpened.current.has(current)) return
    filesOpened.current.add(current)
    if (!isVisible("artifacts")) showPanel("artifacts")
  }, [current, fileCount])
  useEffect(() => {                 // first checklist in a thread: show the sidebar once
    if (current && hasChecklist && !autoOpened.current.has(current)) {
      autoOpened.current.add(current)
      if (!isVisible("checklist") && !isVisible("artifacts")) showPanel("checklist")   // never cover an open artifact
    }
  }, [current, hasChecklist])

  useEffect(() => {
    document.documentElement.classList.add("dark")
    applyDensity(storedDensity())
    init()
  }, [init])
  const density = useApp((s) => (s.settings?.ui as any)?.density as Density | undefined)
  useEffect(() => { if (density) applyDensity(density) }, [density])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.altKey && !e.shiftKey && e.key.toLowerCase() === "b") { e.preventDefault(); toggleTray(); return }
      if (e.ctrlKey && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "n") {     // Ctrl+N: a new chat
        e.preventDefault(); useApp.getState().select(null); return
      }
      if (e.ctrlKey && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "b") {     // Ctrl+B: the left sidebar
        e.preventDefault(); useApp.getState().set({ sidebarOpen: !useApp.getState().sidebarOpen }); return
      }
      if (e.key === "Escape" && current && useApp.getState().threads.find((t) => t.id === current)?.running) {
        fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conv_id: current }) })
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [current])

  if (!ready) return (
    <div className="flex h-full flex-col bg-background">
      <WindowFrame />
      <div className="grid flex-1 place-items-center text-ui text-muted-foreground">Starting…</div>
    </div>
  )
  return (
    <TooltipProvider delay={300}>
      <div className="flex h-full flex-col overflow-hidden bg-background text-foreground">
        <ResizeEdges />
        <div className="relative flex min-h-0 flex-1 overflow-hidden">
          {side !== "closed" && (
            <div className={cn("h-full shrink-0", side === "closing" && "absolute inset-y-0 left-0 z-30",
              side === "opening" && "zen-side-in", side === "closing" && "zen-side-out")}
              onAnimationEnd={(e) => { if (e.target === e.currentTarget) setSide(side === "closing" ? "closed" : "open") }}>
              <Sidebar />
            </div>
          )}
          <main className={cn("flex h-full min-w-0 flex-1 flex-col",
              side === "opening" && "zen-main-from-left", side === "closing" && "zen-main-from-right")}>
            <TopBar />
            <Workspace />
          </main>
        </div>
        <StatusBar />
      </div>
      <SettingsDialog />
      <HelpDialog />
      <WelcomeDialog />
      <Clip />
      <Toaster theme="dark" position="bottom-right" richColors closeButton />
    </TooltipProvider>
  )
}
