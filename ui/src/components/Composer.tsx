import { speaker } from "@/lib/tts"
import { useCallback, useEffect, useRef, useState } from "react"
import { FormulaEditor } from "./FormulaEditor"
import { SlashMenu, type SlashCommand } from "./SlashMenu"
import { Recorder, transcribe } from "@/lib/mic"
import { ArrowUp, AudioLines, Gauge, Check, ChevronDown, Eye, FilePen, Globe, GlobeOff, Hand, ShieldAlert, Zap, Mic, Sigma, ShieldCheck, ShieldOff, FileSpreadsheet, FileText, Image as ImageIcon, Loader2, Paperclip, Presentation, Square, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { api } from "@/lib/api"
import { useApp } from "@/lib/store"
import type { Attachment } from "@/lib/types"
import { fmtBytes } from "@/lib/paths"
import { cn } from "@/lib/utils"

type Pending = Attachment & { uploading?: boolean; key: string }

/** Effort: how much reasoning the agent may spend per step. Auto (default): the agent sizes each request itself —
 *  how much to plan, how hard to think on each step. The tiers cap it; Max thinks hard on every step. */
const EFFORT = [
  { v: "auto", label: "Auto", hint: "The agent decides per request and per step (recommended)" },
  { v: "minimal", label: "Minimal", hint: "No thinking: fastest" },
  { v: "low", label: "Low", hint: "Up to ~1k thinking tokens per step" },
  { v: "medium", label: "Medium", hint: "Up to ~4k thinking tokens per step" },
  { v: "high", label: "High", hint: "Up to ~12k thinking tokens per step" },
  { v: "max", label: "Max", hint: "Thinks hard on every step: slowest, most thorough" },
]
const LEGACY_EFFORT: Record<string, string> = { off: "minimal", standard: "medium", short: "medium", medium: "medium", long: "high", unlimited: "auto" }

const iconFor = (kind: string) =>
  kind === "image" ? ImageIcon : kind === "excel" || kind === "table" ? FileSpreadsheet : kind === "powerpoint" ? Presentation : FileText

export function Composer() {
  const current = useApp((s) => s.current)
  const settings = useApp((s) => s.settings)
  const models = useApp((s) => s.models)
  const running = useApp((s) => s.threads.find((t) => t.id === s.current)?.running ?? false)
  const saveSettings = useApp((s) => s.saveSettings)
  const [text, setText] = useState("")
  const [atts, setAtts] = useState<Pending[]>([])
  const [drag, setDrag] = useState(false)
  const [formula, setFormula] = useState(false)
  const speakingNow = useApp((s) => !!(s as any).speaking)
  const ta = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const mode = settings?.ui.mode ?? "agent"
  const plan = !!settings?.agent.plan_mode
  const effort = String((settings?.agent as any)?.effort || LEGACY_EFFORT[String(settings?.agent.think_level || "")] || "auto")
  // ---- voice input: click-to-talk, and hands-free conversation in speech mode
  const rec = useRef<Recorder | null>(null)
  const [recording, setRecording] = useState(false)
  const [hearing, setHearing] = useState(false)
  const [level, setLevel] = useState(0)
  const [transcribing, setTranscribing] = useState(false)
  const [convo, setConvo] = useState(false)
  const speakingAny = useApp((s) => !!(s as any).speaking)
  const listen = async (vad: boolean) => {
    if (rec.current?.active) return
    rec.current = new Recorder()
    setRecording(true)
    setHearing(false)
    try {
      const wav = await rec.current.record({ vad, onLevel: setLevel, onSpeech: () => setHearing(true) })
      setRecording(false)
      setLevel(0)
      if (!wav) return
      setTranscribing(true)
      const said = await transcribe(wav)
      setTranscribing(false)
      if (!said) return
      if (mode === "speech") send(said)
      else setText((t) => (t ? t.trimEnd() + " " : "") + said)
    } catch (e: any) {
      setRecording(false)
      setTranscribing(false)
      setConvo(false)
      toast.error(e?.name === "NotAllowedError" ? "Microphone access was blocked" : `Voice input failed: ${e?.message || e}`)
    }
  }
  const toggleMic = () => (rec.current?.active ? rec.current.stop() : listen(mode === "speech"))
  useEffect(() => {                // hands-free: listen again whenever the assistant is done thinking and speaking
    if (!convo || mode !== "speech" || running || speakingAny || recording || transcribing) return
    const id = setTimeout(() => listen(true), 350)
    return () => clearTimeout(id)
  }, [convo, mode, running, speakingAny, recording, transcribing])
  useEffect(() => { if (!convo) rec.current?.stop(true) }, [convo])
  useEffect(() => { if (mode !== "speech") setConvo(false) }, [mode])

  useEffect(() => {
    const el = ta.current
    if (!el) return
    const fit = () => {
      el.style.height = "0px"
      el.style.height = Math.min(el.scrollHeight, 260) + "px"
    }
    fit()
    // Re-fit when the panel's width changes: the first measure can happen while the dock is still laying out.
    let w = el.clientWidth
    const ro = new ResizeObserver(() => { if (el.clientWidth !== w) { w = el.clientWidth; fit() } })
    ro.observe(el)
    return () => ro.disconnect()
  }, [text])
  useEffect(() => ta.current?.focus(), [current])

  const addFiles = useCallback(async (files: File[]) => {
    if (!files.length) return
    const temp: Pending[] = files.map((f) => ({ name: f.name, path: "", kind: "unknown", size: f.size, uploading: true, key: `${f.name}-${Math.random()}` }))
    setAtts((a) => [...a, ...temp])
    try {
      const up = await api.upload(files)
      setAtts((a) => [...a.filter((x) => !temp.includes(x)), ...up.map((u) => ({ ...u, key: u.path }))])
    } catch (e: any) {
      toast.error(`Upload failed: ${e.message}`)
      setAtts((a) => a.filter((x) => !temp.includes(x)))
    }
  }, [])

  useEffect(() => {
    const over = (e: DragEvent) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); setDrag(true) } }
    const leave = (e: DragEvent) => { if (!e.relatedTarget) setDrag(false) }
    const drop = (e: DragEvent) => {
      if (!e.dataTransfer?.files.length) return
      e.preventDefault()
      setDrag(false)
      addFiles(Array.from(e.dataTransfer.files))
    }
    window.addEventListener("dragover", over)
    window.addEventListener("dragleave", leave)
    window.addEventListener("drop", drop)
    return () => { window.removeEventListener("dragover", over); window.removeEventListener("dragleave", leave); window.removeEventListener("drop", drop) }
  }, [addFiles])

  // ---- slash commands: type "/" for the list
  const [slashIdx, setSlashIdx] = useState(0)
  const [slashClosed, setSlashClosed] = useState(false)
  const commands: SlashCommand[] = [
    { name: "compact", hint: "Compress this conversation now (frees up context)", run: () => {
      if (!current) return toast.message("Nothing to compress — this is a new chat")
      if (mode === "speech") return toast.message("Speech mode only uses the last few exchanges — nothing to compress")
      api.send({ conv_id: current, text: "/compact", mode })
        .then(() => toast.message("Compressing the conversation…"))
        .catch((e: any) => toast.error(e.message))
    } },
    { name: "new", hint: "Start a new chat", run: () => { useApp.getState().select(null) } },
    { name: "comfort", hint: "A story: a little clam's hero's journey to learn the true meaning of friendship", run: () => {
      api.send({ conv_id: current, text: "/comfort", mode })
        .then((r: any) => { if (r.conv_id !== current) useApp.getState().select(r.conv_id) })
        .catch((e: any) => toast.error(e.message))
    } },
    { name: "stop", hint: "Stop the running task", run: () => {
      if (current) fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conv_id: current }) })
    } },
    { name: "agent", hint: "Switch to Agent mode", run: () => saveSettings({ ui: { ...settings!.ui, mode: "agent" } }) },
    { name: "chat", hint: "Switch to Chat mode", run: () => saveSettings({ ui: { ...settings!.ui, mode: "chat" } }) },
    { name: "speech", hint: "Switch to Speech mode (small fast model, read aloud)", run: () => saveSettings({ ui: { ...settings!.ui, mode: "speech" } as any }) },
    { name: "about", hint: "About Zen Harness and licences", run: () => useApp.getState().set({ helpOpen: true, helpPage: "about" } as any) },
    { name: "docs", hint: "Open the help pages", run: () => useApp.getState().set({ helpOpen: true, helpPage: "getting-started" } as any) },
    { name: "help", hint: "List these commands", run: () => toast.message("Commands", {
      description: commands.filter((c) => c.name !== "help").map((c) => `/${c.name} — ${c.hint}`).join("\n"), duration: 9000 }) },
  ]
  const slashQuery = /^\/[a-z]*$/i.test(text) ? text.slice(1).toLowerCase() : null
  const slashItems = slashQuery === null || slashClosed ? [] : commands.filter((c) => c.name.startsWith(slashQuery))
  useEffect(() => { setSlashIdx(0); if (!text.startsWith("/")) setSlashClosed(false) }, [text])
  const runSlash = (c: SlashCommand) => { setText(""); setSlashClosed(false); c.run() }

  const insertFormula = (latex: string, block: boolean) => {
    const el = ta.current
    const snippet = block ? `\n$$\n${latex}\n$$\n` : `$${latex}$`
    const at = el ? el.selectionStart ?? text.length : text.length
    const end = el ? el.selectionEnd ?? at : at
    const next = text.slice(0, at) + snippet + text.slice(end)
    setText(next)
    setFormula(false)
    setTimeout(() => { if (el) { el.focus(); el.selectionStart = el.selectionEnd = at + snippet.length } }, 0)
  }

  const send = async (override?: string) => {
    const t = (typeof override === "string" ? override : text).trim()
    const ready = atts.filter((a) => !a.uploading)
    if ((!t && !ready.length) || atts.some((a) => a.uploading)) return
    if (running && mode === "chat") return
    setText("")
    setAtts([])
    if (!current) window.dispatchEvent(new Event("zen:submit"))   // the empty-state ensō blows away
    try {
      const r = await api.send({ conv_id: current, text: t, mode, attachments: ready,
        model: settings?.ui.chat_model || models[0], think: settings?.ui.think })
      if (r.conv_id !== current) await useApp.getState().select(r.conv_id)
      if (r.result === "interjected") toast.message("Sent to the running agent", { description: "It will read this at its next step." })
    } catch (e: any) {
      toast.error(e.message)
      setText(t)
    }
  }

  return (
    <div className="relative mx-auto w-full max-w-3xl px-6 pb-5">
      {drag && (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/85">
          <div className="rounded-panel border-2 border-dashed border-info/60 px-10 py-8 text-center">
            <Paperclip className="mx-auto mb-2 size-6 text-info" />
            <div className="text-chat font-medium">Drop files to attach</div>
            <div className="text-caption text-muted-foreground">PDF, Word, Excel, PowerPoint, CSV, images, text</div>
          </div>
        </div>
      )}
      <div className={cn("@container rounded-panel border bg-surface-1 shadow-[0_10px_34px_-14px_rgb(0_0_0/0.6)] transition-[border-color,box-shadow] duration-200",
        running ? "border-info/30"
          : "border-border focus-within:border-[color-mix(in_oklab,var(--brand-a)_40%,transparent)] focus-within:shadow-[0_10px_34px_-14px_rgb(0_0_0/0.6),0_0_0_3px_color-mix(in_oklab,var(--brand-b)_12%,transparent)]")}>
        {atts.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-3">
            {atts.map((a) => {
              const Icon = iconFor(a.kind)
              return (
                <div key={a.key} className="flex items-center gap-2 rounded-lg border border-border bg-muted/50 py-1 pl-2 pr-1">
                  {a.uploading ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" /> : <Icon className="size-3.5 text-muted-foreground" />}
                  <span className="max-w-44 truncate text-caption">{a.name}</span>
                  <span className="text-meta text-muted-foreground">{fmtBytes(a.size)}</span>
                  <button aria-label="Remove attachment" onClick={() => setAtts((x) => x.filter((y) => y !== a))} className="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground">
                    <X className="size-3" />
                  </button>
                </div>
              )
            })}
          </div>
        )}
        {speakingNow && (
          <button onClick={() => speaker.stop()} className="absolute -top-9 right-2 z-20 flex items-center gap-1.5 rounded-full border border-border bg-popover px-3 py-1 text-caption text-muted-foreground shadow hover:text-foreground">
            <span className="size-2 animate-pulse rounded-full bg-info" /> Reading aloud · stop
          </button>
        )}
        <SlashMenu items={slashItems} active={slashIdx} onPick={runSlash} />
        {formula && <FormulaEditor onInsert={insertFormula} onClose={() => setFormula(false)} />}
        <textarea
          ref={ta}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (slashItems.length) {
              if (e.key === "ArrowDown") { e.preventDefault(); setSlashIdx((i) => (i + 1) % slashItems.length); return }
              if (e.key === "ArrowUp") { e.preventDefault(); setSlashIdx((i) => (i - 1 + slashItems.length) % slashItems.length); return }
              if (e.key === "Escape") { e.preventDefault(); setSlashClosed(true); return }
              if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") { e.preventDefault(); runSlash(slashItems[slashIdx]); return }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              const exact = commands.find((c) => text.trim().toLowerCase() === "/" + c.name)
              if (exact) return runSlash(exact)
              send()
            }
          }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files)
            if (files.length) { e.preventDefault(); addFiles(files) }
          }}
          rows={1}
          placeholder={running ? (mode === "agent" ? "Add a message for the agent — it reads it at the next step" : "Waiting for the reply…")
            : mode === "agent" ? "Give the agent a task…  (type / for commands)" : mode === "speech" ? "Ask out loud… (answers are verified with search and read aloud)" : "Ask anything…"}
          className="block max-h-[260px] w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-chat leading-relaxed outline-none placeholder:text-muted-foreground/70"
        />
        <div className="@container flex items-center gap-1 px-2 pb-2">
          <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.target.value = "" }} />
          <Tooltip>
            <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Attach files" onClick={() => fileInput.current?.click()} />}>
              <Paperclip />
            </TooltipTrigger>
            <TooltipContent>Attach files (or drop / paste them)</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger render={<Button variant={recording ? "secondary" : "ghost"} size="icon-sm" aria-label="Dictate" onClick={toggleMic} />}>
              {transcribing ? <Loader2 className="animate-spin" /> : (
                <span className="relative grid place-items-center">
                  <Mic className={recording ? "text-destructive" : ""} />
                  {recording && <span className="absolute -inset-1.5 rounded-full border-2 border-destructive/60" style={{ transform: `scale(${1 + level * 0.6})` }} />}
                </span>
              )}
            </TooltipTrigger>
            <TooltipContent>{recording ? (hearing ? "Listening… click to stop" : "Waiting for you to speak…") : mode === "speech" ? "Talk (stops when you pause)" : "Dictate"}</TooltipContent>
          </Tooltip>
          {mode === "speech" && (
            <Tooltip>
              <TooltipTrigger render={<Button variant={convo ? "secondary" : "ghost"} size="sm" onClick={() => setConvo((c) => !c)}
                className={convo ? "text-info" : ""} />}>
                <AudioLines className={convo && recording ? "animate-pulse" : ""} /> {convo ? (recording ? (hearing ? "Listening" : "Your turn") : running ? "Thinking" : speakingAny ? "Speaking" : "On") : "Conversation"}
              </TooltipTrigger>
              <TooltipContent>Hands-free: it listens, answers out loud, then listens again. Click to end.</TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger render={<Button variant={formula ? "secondary" : "ghost"} size="icon-sm" aria-label="Insert formula" onClick={() => setFormula((f) => !f)} />}>
              <Sigma />
            </TooltipTrigger>
            <TooltipContent>Insert a formula (or read one from an image)</TooltipContent>
          </Tooltip>
          <div className="ml-1 flex rounded-lg bg-muted p-0.5 text-caption">
            {(["agent", "chat", "speech"] as const).map((m) => (
              <button key={m} onClick={() => saveSettings({ ui: { ...settings!.ui, mode: m } })}
                className={cn("rounded-md px-1.5 py-0.5 capitalize @2xl:px-2.5 transition-colors",
                  mode === m ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                {m}
              </button>
            ))}
          </div>
          {mode === "speech" && (
            <select value={(settings?.ui as any)?.speech_model || "qwen3:1.7b"}
              onChange={(e) => saveSettings({ ui: { ...settings!.ui, speech_model: e.target.value } as any })}
              title="Speech-mode model (runs alone on the GPU; agent models are unloaded)"
              className="ml-1 cursor-pointer rounded-lg bg-transparent px-1.5 py-1 text-caption text-muted-foreground outline-none hover:bg-surface-2">
              <option value="qwen3:1.7b" className="bg-popover">Qwen3 1.7B</option>
              <option value="qwen3:0.6b" className="bg-popover">Qwen3 0.6B (fastest)</option>
            </select>
          )}
          {mode === "agent" && (
            <Tooltip>
              <TooltipTrigger render={
                <label className="ml-0.5 flex cursor-pointer items-center gap-1 rounded-lg px-1.5 @2xl:ml-1 @2xl:px-2 py-1 text-caption text-muted-foreground hover:bg-surface-2 hover:text-foreground" />}>
                <Gauge className="size-3.5" />
                <span className="hidden @2xl:inline">Effort</span>
                <select value={effort} onChange={(e) => saveSettings({ agent: { effort: e.target.value } as any })}
                  className="cursor-pointer bg-transparent outline-none">
                  {EFFORT.map((l) => <option key={l.v} value={l.v} className="bg-popover">{l.label}</option>)}
                </select>
              </TooltipTrigger>
              <TooltipContent>How much effort the agent may spend. It sizes each request itself — quick, standard or deep:
                how much to plan, how hard to think on each step; the tiers below Auto cap it. {EFFORT.find((l) => l.v === effort)?.hint}. Changing it reloads the model.</TooltipContent>
            </Tooltip>
          )}
          {mode === "agent" && <PermissionMenu tier={plan ? "read" : String((settings?.agent as any)?.permissions || "ask")}
            onPick={(v) => saveSettings({ agent: (v === "read" ? { plan_mode: true } : { plan_mode: false, permissions: v }) as any })} />}
          {mode === "agent" && (() => {
            const web = (settings?.agent as any)?.web !== false
            return (
              <Tooltip>
                <TooltipTrigger render={
                  <button onClick={() => saveSettings({ agent: { web: !web } as any })} aria-pressed={web}
                    className={"ml-0.5 flex shrink-0 items-center gap-1 rounded-lg px-1.5 @2xl:ml-1 @2xl:px-2 py-1 text-caption transition-colors hover:bg-surface-2 " +
                      (web ? "text-muted-foreground hover:text-foreground" : "text-warning")} />}>
                  {web ? <Globe className="size-3.5" /> : <GlobeOff className="size-3.5" />}
                  <span className="hidden whitespace-nowrap @2xl:inline">{web ? "Web" : "No web"}</span>
                </TooltipTrigger>
                <TooltipContent>{web
                  ? "On: the agent can search the web, read pages and use the browser. Click to turn web access off."
                  : "Off: web search, page fetch and the browser are switched off for the agent — it is told so, and any web call is refused. Offline Wikipedia and your files still work. Click to turn on."}</TooltipContent>
              </Tooltip>
            )
          })()}
          {mode === "agent" && (() => {
            const verify = (settings?.agent as any)?.verify !== false
            return (
              <Tooltip>
                <TooltipTrigger render={
                  <button onClick={() => saveSettings({ agent: { verify: !verify } as any })} aria-pressed={verify}
                    className={"ml-0.5 flex shrink-0 items-center gap-1 rounded-lg px-1.5 @2xl:ml-1 @2xl:px-2 py-1 text-caption transition-colors hover:bg-surface-2 " +
                      (verify ? "text-muted-foreground hover:text-foreground" : "text-warning")} />}>
                  {verify ? <ShieldCheck className="size-3.5" /> : <ShieldOff className="size-3.5" />}
                  <span className="hidden whitespace-nowrap @2xl:inline">{verify ? "Verify" : "No checks"}</span>
                </TooltipTrigger>
                <TooltipContent>{verify
                  ? "On: every message gets a background search, and answers are checked against sources (claims, figures) — slower, sometimes a redraft. Click to turn off."
                  : "Off: no background research and no answer checks — the first answer goes out as written. Fastest; facts are not verified. Click to turn on."}</TooltipContent>
              </Tooltip>
            )
          })()}
          {mode === "chat" && (
            <select value={settings?.ui.chat_model || models[0]} onChange={(e) => saveSettings({ ui: { ...settings!.ui, chat_model: e.target.value } })}
              className="ml-1 max-w-48 truncate rounded-lg bg-transparent px-2 py-1 text-caption text-muted-foreground outline-none hover:bg-surface-2">
              {models.map((m) => <option key={m} value={m} className="bg-popover">{m}</option>)}
            </select>
          )}
          <div className="flex-1" />
          <span className="mr-2 hidden whitespace-nowrap label-tech text-muted-foreground/50 @[720px]:inline">{running ? "" : "Enter ↵ send · Shift+Enter newline"}</span>
          {running && (
            <Button size="icon-sm" variant="secondary" onClick={() => current && api.stop(current)} title="Stop" aria-label="Stop">
              <Square className="size-3.5 fill-current" />
            </Button>
          )}
          <Button size="icon-sm" onClick={() => send()} disabled={!text.trim() && !atts.length} className="rounded-lg" title="Send" aria-label="Send">
            <ArrowUp />
          </Button>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ permission tiers */

const TIERS = [
  { v: "read", label: "Read only", Icon: Eye, tone: "", hint: "Investigates and plans; every change, command and input is refused." },
  { v: "ask", label: "Ask first", Icon: Hand, tone: "", hint: "Reads and searches freely; asks before writing files, running commands or using the screen." },
  { v: "edits", label: "Auto-edit", Icon: FilePen, tone: "", hint: "Writes and edits files in your allowed folders without asking; still asks before commands." },
  { v: "auto", label: "Auto", Icon: Zap, tone: "warning", hint: "Files and commands run without asking; still asks for mouse/keyboard control, MCP tools and cloud calls." },
  { v: "bypass", label: "Bypass", Icon: ShieldAlert, tone: "destructive", hint: "Never asks. Commands the safety policy blocks as damaging are still refused." },
] as const

/** The agent's permission tier: how much it may do without asking. */
function PermissionMenu({ tier, onPick }: { tier: string; onPick: (v: string) => void }) {
  const cur = TIERS.find((t) => t.v === tier) ?? TIERS[1]
  return (
    <DropdownMenu>
      <DropdownMenuTrigger title={`Permissions — ${cur.label}: ${cur.hint}`} data-tone={cur.tone || undefined}
        className={cn("ml-0.5 flex shrink-0 items-center gap-1 rounded-lg px-1.5 @2xl:ml-1 @2xl:px-2 py-1 text-caption transition-colors hover:bg-surface-2",
          cur.tone ? "tone-text" : "text-muted-foreground hover:text-foreground", tier === "bypass" && "tone-soft")}>
        <cur.Icon className="size-3.5" />
        <span className="hidden whitespace-nowrap @2xl:inline">{cur.label}</span>
        <ChevronDown className="size-3 opacity-60" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-80">
        <DropdownMenuGroup>
        <DropdownMenuLabel>Permissions</DropdownMenuLabel>
        {TIERS.map((t) => (
          <DropdownMenuItem key={t.v} onClick={() => onPick(t.v)} className="items-start gap-2.5 py-1.5">
            <t.Icon data-tone={t.tone || undefined} className={cn("mt-0.5 size-4 shrink-0", t.tone ? "tone-text" : "text-muted-foreground")} />
            <span className="min-w-0 flex-1">
              <span className="block text-ui font-medium">{t.label}</span>
              <span className="block text-caption text-muted-foreground">{t.hint}</span>
            </span>
            {t.v === tier ? <Check className="mt-0.5 size-3.5 shrink-0" /> : <span className="w-3.5 shrink-0" />}
          </DropdownMenuItem>
        ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <div className="px-2 py-1 text-meta text-muted-foreground">Applies to this app's chats; API runs keep their own allow-list.</div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
