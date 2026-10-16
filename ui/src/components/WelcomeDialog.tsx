import { useEffect, useState } from "react"
import { Cloud, Cpu, Download, FileUp, Loader2, Sparkles } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { help, modelsApi, type About, type Download as Dl } from "@/lib/api"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"

/* First run: no model configured yet. Checks the hardware, recommends a model from the catalog
   (backend/model_catalog.json), downloads it and sets it up (models_api.install_from_catalog saves the profile and
   makes it the default when the download finishes), or lets the user bring their own file / a cloud key. */

type CatalogModel = {
  id: string; name: string; summary: string; file: string; size: number; license: string; license_url: string
  installed: boolean; fits: boolean; recommended?: boolean; extras: { file: string; size: number; what: string }[]
}
const gb = (b: number) => `${(b / 1024 ** 3).toFixed(1)} GB`
const DISMISS_KEY = "zen.welcome.dismissed"

function dismissed() {
  try { return localStorage.getItem(DISMISS_KEY) === "1" } catch { return false }
}

export function WelcomeDialog() {
  const settings = useApp((s) => s.settings)
  const set = useApp((s) => s.set)
  const needsModel = !!settings && settings.agent.backend !== "openai" && !settings.agent.model
  const [open, setOpen] = useState(false)
  const [about, setAbout] = useState<About | null>(null)
  const [cat, setCat] = useState<CatalogModel[]>([])
  const [dls, setDls] = useState<Dl[]>([])
  const [keyFor, setKeyFor] = useState<"" | "openai" | "anthropic">("")
  const [key, setKey] = useState("")
  useEffect(() => { if (needsModel && !dismissed()) setOpen(true) }, [needsModel])
  useEffect(() => {
    if (!open) return
    help.about().then(setAbout).catch(() => {})
    fetch("/api/models/catalog").then((r) => r.json()).then((d) => setCat(d.models || [])).catch(() => {})
    const t = window.setInterval(() => modelsApi.downloads().then((d) => setDls(d.downloads)).catch(() => {}), 1200)
    return () => window.clearInterval(t)
  }, [open])
  useEffect(() => {          // the backend set a default model (download finished and set up): done
    if (open && settings?.agent.model) {
      toast.success("Your model is ready", { description: `${settings.agent.model} — say hello!` })
      setOpen(false)
    }
  }, [settings?.agent.model])
  const close = (forever: boolean) => {
    if (forever) { try { localStorage.setItem(DISMISS_KEY, "1") } catch { /* private mode */ } }
    setOpen(false)
  }
  const install = async (id: string) => {
    const r = await fetch("/api/models/catalog/install", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, make_default: true }) }).then((x) => x.json())
    if (!r.ok) toast.error(r.error || "Could not start the download")
  }
  const saveKey = async () => {
    const r = await fetch("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: keyFor, key }) }).then((x) => x.json())
    if (r.error) { toast.error(r.error); return }
    toast.success("Key saved", { description: "Choose a cloud model in Settings → General." })
    setKey(""); close(true)
    await useApp.getState().init()
    set({ settingsOpen: true, settingsTab: "general" } as any)
  }
  const active = dls.find((d) => d.status !== "done" && d.status !== "failed" && d.status !== "paused" && d.id.toLowerCase().endsWith(".gguf") && !d.id.toLowerCase().startsWith("mmproj"))
  const failed = dls.find((d) => d.status === "failed")
  const gpu = about?.gpu
  return (
    <Dialog open={open} onOpenChange={(v) => !v && close(false)}>
      <DialogContent className="max-h-[min(90dvh,860px)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Sparkles className="size-4" /> Welcome to Zazen</DialogTitle></DialogHeader>
        <p className="text-ui text-muted-foreground">Zazen runs AI models on your own PC. To start, it needs a model — pick one below (it downloads once and stays on your computer).</p>
        <div className="flex items-center gap-2 rounded-lg border border-line-1 bg-surface-1 px-3 py-2 text-ui">
          <Cpu className="size-4 text-muted-foreground" />
          {!about ? "Checking your hardware…" : <span>{gpu ? `${gpu.name} · ${(gpu.vram_mib / 1024).toFixed(0)} GB video memory` : "No NVIDIA graphics card found — models will run on the CPU (slower)"}
            {about.ram_mib ? ` · ${(about.ram_mib / 1024).toFixed(0)} GB RAM` : ""}</span>}
        </div>

        {active ? (
          <div className="rounded-lg border border-info/40 px-3 py-3">
            <div className="flex items-center gap-2 text-ui"><Loader2 className="size-4 animate-spin" />
              {active.status === "setting up" ? "Setting it up for your hardware…" : `Downloading ${active.id}`}</div>
            {active.total > 0 && <>
              <div className="mt-2 h-1.5 overflow-hidden rounded bg-surface-3"><div className="h-full bg-info transition-all" style={{ width: `${(100 * active.done) / active.total}%` }} /></div>
              <div className="mt-1 text-caption text-muted-foreground">{gb(active.done)} of {gb(active.total)} · you can keep using the app; it finishes in the background</div>
            </>}
          </div>
        ) : (
          <div className="space-y-2">
            {cat.map((m) => (
              <div key={m.id} className={cn("rounded-lg border px-3 py-2.5", m.recommended ? "border-success/50 bg-success/5" : "border-line-1", !m.fits && "opacity-60")}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-ui font-medium">{m.name}{m.recommended && <span className="ml-2 rounded bg-success/15 px-1.5 text-caption text-success">recommended for your PC</span>}
                      {!m.fits && <span className="ml-2 text-caption text-warning">may not fit your PC</span>}</div>
                    <div className="text-caption text-muted-foreground">{m.summary}</div>
                    <div className="mt-0.5 text-caption text-muted-foreground">{gb(m.size + m.extras.reduce((s, e) => s + e.size, 0))} download · licence {m.license}</div>
                  </div>
                  <Button size="sm" variant={m.recommended ? "default" : "outline"} onClick={() => install(m.id)}>
                    <Download /> {m.installed ? "Use" : "Download"}</Button>
                </div>
              </div>
            ))}
            {failed && <div className="text-caption text-destructive">Download failed: {failed.error}. Check your internet connection and try again.</div>}
          </div>
        )}

        <div className="mt-1 flex flex-wrap gap-2 border-t border-line-1 pt-3">
          <Button size="sm" variant="outline" onClick={() => { close(false); set({ settingsOpen: true, settingsTab: "models" } as any) }}><FileUp /> I have a model file</Button>
          <Button size="sm" variant="outline" onClick={() => setKeyFor(keyFor ? "" : "openai")}><Cloud /> Use a cloud model</Button>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => close(true)}>Not now</Button>
        </div>
        {keyFor && (
          <div className="space-y-2 rounded-lg border border-line-1 p-3">
            <div className="flex gap-2 text-caption">
              {(["openai", "anthropic"] as const).map((p) => (
                <button key={p} onClick={() => setKeyFor(p)} className={cn("rounded-md px-2 py-1", keyFor === p ? "bg-surface-3" : "text-muted-foreground")}>{p === "openai" ? "OpenAI" : "Anthropic"}</button>
              ))}
            </div>
            <div className="flex gap-1.5">
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={keyFor === "openai" ? "sk-…" : "sk-ant-…"}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-ui outline-none focus:border-ring" />
              <Button size="sm" onClick={saveKey} disabled={key.trim().length < 20}>Save key</Button>
            </div>
            <div className="text-caption text-muted-foreground">Stored in your data folder on this PC. Cloud models are billed by the provider, and what you send goes to them.</div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
