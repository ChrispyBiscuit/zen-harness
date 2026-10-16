import { useEffect, useState } from "react"
import { Cpu, Download, Loader2, RefreshCw, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { confirmDownload } from "@/components/ConsentDialog"
import { Field, Section, Toggle, inputCls } from "@/components/SettingsDialog"

/* This PC's GPU (NVIDIA, AMD or Intel) picks the llama.cpp runtime, the model file, context, cache and where vision
   runs (backend/hardware.py, plans/hardware-scaling.md). Setup downloads what the plan needs; every field can be set
   by hand, and a value set here always wins over the detected one. */

export type Hw = Record<string, any>
type Gpu = { vendor: string; name: string; vram_mib: number; driver: string; integrated: boolean }
type Job = { status: string; step?: string; done?: number; total?: number; error?: string; backend?: string; notes?: string[] }
type Report = {
  gpus: Gpu[]; gpu: Gpu | null; ram_mib: number; order: string[]; supported: boolean; runtimes: Record<string, boolean>
  plan: { backend: string; model_file?: string; better_file?: string; num_ctx: number; kv_cache: string; kv_cache_v: string
    vision_gpu: boolean; note: string }
  downloads: { kind: string; name: string; mib: number }[]
  wiki: { installed: boolean; mib: number; file: string }; job: Job
}

const BACKENDS: [string, string][] = [["auto", "Automatic"], ["cuda", "CUDA (NVIDIA)"], ["hip", "HIP (AMD)"],
  ["vulkan", "Vulkan (any GPU)"], ["sycl", "SYCL (Intel)"], ["cpu", "CPU only (very slow)"]]
const CTX: [string, string][] = [["auto", "Automatic"], ["32768", "32K"], ["65536", "64K"], ["98304", "96K"], ["131072", "128K"]]
const KV: [string, string][] = [["auto", "Automatic"], ["q8_0", "8-bit"], ["q4_0", "4-bit (more context)"], ["f16", "16-bit"]]
const STEP: Record<string, string> = { runtime: "Getting the GPU runtime", model: "Downloading the model",
  vision: "Downloading the vision file", wiki: "Downloading offline Wikipedia" }
const gib = (mib: number) => `${(mib / 1024).toFixed(mib >= 10240 ? 0 : 1)} GB`

export function SetupProgress({ job }: { job: Job }) {
  if (!job || job.status === "idle") return null
  const step = (job.step || "").split(":")[0]
  const pct = job.total ? Math.min(100, (100 * (job.done || 0)) / job.total) : 0
  return (
    <div className="rounded-lg border border-line-1 px-3 py-2.5 text-ui">
      {job.status === "running" && <>
        <div className="flex items-center gap-2"><Loader2 className="size-4 animate-spin" />{STEP[step] || "Setting up"}
          {job.step?.includes(":") ? ` (${job.step.split(":")[1].toUpperCase()})` : ""}</div>
        {!!job.total && <>
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-surface-3"><div className="h-full bg-info transition-all" style={{ width: `${pct}%` }} /></div>
          <div className="mt-1 text-caption text-muted-foreground">{gib((job.done || 0) / 1048576)} of {gib(job.total / 1048576)} · it continues in the background</div>
        </>}
      </>}
      {job.status === "done" && <div className="text-success">Set up for this PC{job.backend ? ` (${job.backend.toUpperCase()})` : ""}.</div>}
      {job.status === "failed" && <div className="text-destructive">Setup stopped: {job.error}</div>}
      {job.status === "cancelled" && <div className="text-muted-foreground">Setup paused. Run it again to continue where it stopped.</div>}
      {!!job.notes?.length && <div className="mt-1 text-caption text-muted-foreground">{job.notes.join(" · ")}</div>}
    </div>
  )
}

/** Start setup after a yes in the download dialog. -> true when it started. */
export async function startSetup(r: Report, wiki: boolean, backend = "auto") {
  const mib = r.downloads.reduce((s, d) => s + d.mib, 0) + (wiki && !r.wiki.installed ? r.wiki.mib : 0)
  if (mib > 0 && !await confirmDownload({ title: "Set up Zazen for this PC", size: mib * 1048576,
    source: "github.com/PrismML-Eng, huggingface.co" + (wiki ? ", download.kiwix.org" : ""),
    detail: [...r.downloads.map((d) => `${d.name} (${gib(d.mib)})`),
      ...(wiki && !r.wiki.installed ? [`Offline Wikipedia (${gib(r.wiki.mib)})`] : [])].join(" · ") })) return false
  const res = await fetch("/api/hardware/setup", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wiki, backend }) }).then((x) => x.json())
  if (!res.ok) toast.error(res.job?.status === "running" ? "Setup is already running" : "Could not start setup")
  return !!res.ok
}

export function useHardware(poll: boolean) {
  const [r, setR] = useState<Report | null>(null)
  const [job, setJob] = useState<Job>({ status: "idle" })
  const load = () => fetch("/api/hardware").then((x) => x.json()).then((d: Report) => { setR(d); setJob(d.job) }).catch(() => {})
  useEffect(() => { if (poll) load() }, [poll])
  useEffect(() => {
    if (!poll || job.status !== "running") return
    const t = window.setInterval(() => fetch("/api/hardware/setup").then((x) => x.json()).then((d) => {
      setJob(d.job)
      if (d.job.status !== "running") load()
    }).catch(() => {}), 1000)
    return () => window.clearInterval(t)
  }, [poll, job.status])
  return { r, job, setJob, load }
}

export function HardwarePanel({ hw, setHw }: { hw: Hw; setHw: (patch: Hw) => void }) {
  const { r, job, setJob, load } = useHardware(true)
  const [wiki, setWiki] = useState(true)
  const auto = hw.auto !== false
  const val = (k: string) => String(hw[k] ?? "auto")
  const sel = (k: string, opts: [string, string][], label: string, hint?: string) => (
    <Field label={label} hint={hint}>
      <select value={val(k)} disabled={!auto} className={inputCls}
        onChange={(e) => setHw({ [k]: /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value })}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}{v === "auto" && r ? planHint(k, r) : ""}</option>)}
      </select>
    </Field>
  )
  if (!r) return <div className="flex items-center gap-2 px-1 py-4 text-ui text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Checking this PC…</div>
  const files = [["auto", "Automatic"], ...(["PQ2_0", "PTQ1_0"].map((f) => [`Ternary-Bonsai-2-27B-${f}.gguf`,
    f === "PQ2_0" ? "PQ2_0 (7.2 GB, faster prompts)" : "PTQ1_0 (5.9 GB, fits 8 GB)"]))] as [string, string][]
  return (
    <div className="space-y-4">
      <Section title="This PC">
        <div className="space-y-1.5 rounded-lg border border-line-1 bg-surface-1 px-3 py-2.5 text-ui">
          {r.gpus.length ? r.gpus.map((g, i) => (
            <div key={i} className="flex items-center gap-2"><Cpu className="size-4 text-muted-foreground" />
              <span className={g.integrated ? "text-muted-foreground" : ""}>{g.name} · {g.integrated ? "integrated" : gib(g.vram_mib)}</span>
              {r.gpu?.name === g.name && <span className="rounded bg-success/15 px-1.5 text-caption text-success">used</span>}
            </div>)) : <div>No graphics card found.</div>}
          <div className="text-caption text-muted-foreground">{gib(r.ram_mib)} RAM · runtimes tried in order: {r.order.map((b) => b.toUpperCase()).join(" → ")}</div>
          <div className={r.supported ? "text-caption" : "text-caption text-warning"}>{r.plan.note}</div>
          {!r.supported && <div className="text-caption text-warning">Zazen needs a graphics card with 8 GB or more (NVIDIA, AMD or Intel Arc) to run its model at a usable speed.</div>}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={job.status === "running"} onClick={async () => {
            if (await startSetup(r, wiki, val("backend"))) setJob({ status: "running" })
          }}><Download /> {r.downloads.length || (wiki && !r.wiki.installed) ? "Set up for this PC" : "Check setup again"}</Button>
          {job.status === "running" && <Button size="sm" variant="outline" onClick={() => fetch("/api/hardware/cancel", { method: "POST" })}><X /> Pause</Button>}
          <Button size="sm" variant="ghost" onClick={load}><RefreshCw /> Detect again</Button>
          <label className="ml-auto flex items-center gap-1.5 text-caption text-muted-foreground">
            <input type="checkbox" checked={wiki} onChange={(e) => setWiki(e.target.checked)} />
            Offline Wikipedia ({r.wiki.installed ? "installed" : gib(r.wiki.mib)})</label>
        </div>
        <div className="mt-2"><SetupProgress job={job} /></div>
      </Section>
      <Section title="Settings for this PC">
        <Toggle checked={auto} onChange={(v) => setHw({ auto: v })} label="Choose settings for this hardware automatically"
          hint="Off: the model's profile is used exactly as written. On: the fields below are detected, and any you set by hand win." />
        {sel("backend", BACKENDS, "Runtime", "The llama.cpp build for your GPU. Vulkan works on every vendor; CUDA, HIP and SYCL are each vendor's own.")}
        {sel("gpu", [["auto", "Automatic"], ...r.gpus.map((g, i) => [String(i), g.name] as [string, string])], "Graphics card")}
        {sel("model_file", files, "Model file", "Both files hold the same model and give the same answers; PQ2_0 needs about 11 GB of GPU memory.")}
        {sel("num_ctx", CTX, "Context window", "More context means fewer compactions on long tasks, but each step gets slower as it fills.")}
        {sel("kv_cache", KV, "Cache precision (keys)")}
        {sel("kv_cache_v", KV, "Cache precision (values)")}
        {sel("vision_gpu", [["auto", "Automatic"], ["on", "On the GPU (faster screenshots)"], ["off", "On the CPU (saves GPU memory)"]], "Vision")}
      </Section>
    </div>
  )
}

function planHint(k: string, r: Report) {
  const p = r.plan as Record<string, any>
  const v = k === "backend" ? p.backend?.toUpperCase() : k === "gpu" ? r.gpu?.name
    : k === "num_ctx" ? `${Math.round(p.num_ctx / 1024)}K` : k === "vision_gpu" ? (p.vision_gpu ? "GPU" : "CPU")
    : k === "model_file" ? String(p.model_file || "").replace("Ternary-Bonsai-2-27B-", "").replace(".gguf", "") : p[k]
  return v ? ` (${v})` : ""
}
