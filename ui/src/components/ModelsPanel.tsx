import { useEffect, useRef, useState } from "react"
import { CheckCircle2, Download, FileUp, FolderOpen, Gauge, Loader2, RefreshCw, Trash2, XCircle } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Field, Section, inputCls } from "@/components/SettingsDialog"
import { help, modelsApi, type Download as Dl, type ModelList, type Profile, type Proposal, type TestResult } from "@/lib/api"
import { cn } from "@/lib/utils"
import { confirmDownload } from "@/components/ConsentDialog"

/* Settings → Models: everything about local models in one place — what's in the models folder, saved profiles
   (per-model context / KV cache / MoE offload / helper files), adding a model (pick, drop in the folder, download),
   and a test load that proves a profile fits before it is used. Backend: models_api.py. */

const CTX = [8192, 16384, 32768, 49152, 65536, 98304, 131072, 196608, 262144]
const gb = (b: number) => `${(b / 1024 ** 3).toFixed(1)} GB`

function ProposalEditor({ p, onDone, onUse }: { p: Proposal; onDone: () => void; onUse: (name: string) => void }) {
  const [name, setName] = useState(p.name)
  const [prof, setProf] = useState<Profile>(p.profile)
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState<"" | "test" | "save">("")
  const moe = p.profile.n_cpu_moe !== undefined || !!p.info.experts
  const set = (patch: Partial<Profile>) => { setProf({ ...prof, ...patch }); setTest(null) }
  const runTest = async () => {
    setBusy("test")
    try { setTest(await modelsApi.test(prof)) } catch (e: any) { setTest({ ok: false, error: e.message }) } finally { setBusy("") }
  }
  const save = async (use: boolean) => {
    setBusy("save")
    try {
      const r = await modelsApi.save(name, prof)
      toast.success(`Profile “${r.name}” saved`)
      if (use) onUse(r.name)
      onDone()
    } catch (e: any) { toast.error(e.message) } finally { setBusy("") }
  }
  return (
    <div className="rounded-lg border border-line-1 bg-surface-1 p-3">
      <div className="mb-1 text-ui font-medium">{p.info.name}</div>
      <div className="mb-2 text-caption text-muted-foreground">
        {gb(p.info.size)} · {p.info.architecture || "unknown architecture"}{p.info.layers ? ` · ${p.info.layers} layers` : ""}
        {p.info.experts ? ` · mixture of experts (${p.info.experts})` : " · dense"}{p.info.quant ? ` · ${p.info.quant}` : ""}
        {p.helpers.mmproj ? " · vision file found" : ""}{p.helpers.drafter ? " · speed-up draft model found" : ""}
      </div>
      {p.notes.map((n, i) => <div key={i} className="mb-1 text-caption text-warning">{n}</div>)}
      <Field label="Profile name"><input value={name} onChange={(e) => setName(e.target.value)} className={inputCls + " mono text-caption"} /></Field>
      <Field label="Context length" hint="How much the model keeps in mind. The proposal is the most that fits your GPU.">
        <select value={prof.num_ctx} onChange={(e) => set({ num_ctx: Number(e.target.value) })} className={inputCls}>
          {[...new Set([...CTX, prof.num_ctx || 32768])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{Math.round(n / 1024)}k tokens{n === p.profile.num_ctx ? " (proposed)" : ""}</option>)}
        </select>
      </Field>
      <Field label="KV cache" hint="f16/bf16 most accurate · q8_0 half the memory · q4_0 quarter (can hurt long agent sessions).">
        <select value={prof.kv_cache} onChange={(e) => set({ kv_cache: e.target.value })} className={inputCls}>
          {["f16", "bf16", "q8_0", "q4_0"].map((k) => <option key={k} value={k}>{k}{k === p.profile.kv_cache ? " (proposed)" : ""}</option>)}
        </select>
      </Field>
      {moe && (
        <Field label="CPU expert layers" hint="Layers whose experts stay in system RAM so the rest fits on the GPU. Higher = less GPU memory, slower.">
          <input type="number" min={0} max={200} value={prof.n_cpu_moe ?? 0} onChange={(e) => set({ n_cpu_moe: Number(e.target.value) })} className={inputCls + " w-28"} />
        </Field>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={runTest} disabled={!!busy}>{busy === "test" ? <Loader2 className="animate-spin" /> : <Gauge />} Test load</Button>
        <Button size="sm" variant="outline" onClick={() => save(false)} disabled={!!busy}>Save profile</Button>
        <Button size="sm" onClick={() => save(true)} disabled={!!busy}>Save and use</Button>
        <Button size="sm" variant="ghost" onClick={onDone} disabled={!!busy}>Cancel</Button>
      </div>
      {busy === "test" && <div className="mt-2 text-caption text-muted-foreground">Loading the model (the chat model is unloaded during the test)… large models take a minute.</div>}
      {test && (
        <div className={cn("mt-2 rounded-md border px-2.5 py-1.5 text-caption", test.ok ? "border-success/40 text-success" : "border-destructive/40 text-destructive")}>
          {test.ok
            ? <span className="flex items-center gap-1.5"><CheckCircle2 className="size-3.5" /> Loaded in {test.load_s}s · {test.tokens_per_s} words/s generating · {test.prompt_tokens_per_s} tokens/s reading{test.vram_mib ? ` · ~${(test.vram_mib / 1024).toFixed(1)} GB GPU memory` : ""}</span>
            : <span className="flex items-start gap-1.5"><XCircle className="mt-0.5 size-3.5 shrink-0" /> {test.error}</span>}
          {test.sample && <div className="mt-1 text-muted-foreground">“{test.sample}”</div>}
        </div>
      )}
    </div>
  )
}

export function ModelsPanel({ current, onUse }: { current: string; onUse: (name: string) => void }) {
  const [list, setList] = useState<ModelList | null>(null)
  const [prop, setProp] = useState<Proposal | null>(null)
  const [url, setUrl] = useState("")
  const [dls, setDls] = useState<Dl[]>([])
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | null>(null)
  const load = () => modelsApi.list().then(setList).catch((e) => toast.error(e.message))
  const pollDownloads = () => modelsApi.downloads().then((d) => {
    setDls(d.downloads)
    if (d.downloads.some((x) => x.status === "done")) load()
  }).catch(() => {})
  useEffect(() => {
    load(); pollDownloads()
    timer.current = window.setInterval(pollDownloads, 1500)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [])
  const inspect = async (path: string) => {
    setBusy(true)
    try { setProp(await modelsApi.inspect(path)) } catch (e: any) { toast.error(e.message) } finally { setBusy(false) }
  }
  const pick = async () => {
    const { path } = await modelsApi.pick().catch(() => ({ path: "" }))
    if (path) inspect(path)
  }
  const download = async () => {
    const link = url.trim()
    const file = decodeURIComponent(link.split("?")[0].split("/").pop() || "the model")
    let host = ""
    try { host = new URL(link).host } catch { /* the backend explains a bad link */ }
    if (!await confirmDownload({ title: file, source: host || link,
      detail: "A model file for the models folder. It downloads in the background (you can pause and resume it below)." })) return
    try { await modelsApi.download(link); setUrl(""); toast.success("Download started"); pollDownloads() } catch (e: any) { toast.error(e.message) }
  }
  const removeProfile = async (name: string) => {
    if (!confirm(`Remove the profile “${name}”? The model file stays in the folder.`)) return
    await modelsApi.remove(name).then(load).catch((e: any) => toast.error(e.message))
  }
  const profiled = new Set(Object.values(list?.profiles || {}).map((p) => (p.path || "").toLowerCase()))
  const loose = (list?.files || []).filter((f) => f.kind === "model" && !profiled.has(f.path.toLowerCase()))
  const active = dls.filter((d) => d.status !== "done")

  return (
    <>
      <Section title="Your models">
        {!list ? <div className="text-caption text-muted-foreground">Loading…</div> : (
          <div className="space-y-1.5">
            {Object.entries(list.profiles).length === 0 && loose.length === 0 &&
              <div className="text-caption text-muted-foreground">No models yet. Add one below.</div>}
            {Object.entries(list.profiles).map(([name, p]) => (
              <div key={name} className="flex items-center justify-between gap-2 rounded-lg border border-line-1 bg-surface-1 px-3 py-2">
                <div className="min-w-0">
                  <div className="text-ui">{name}{name === current && <span className="ml-2 rounded bg-success/15 px-1.5 text-caption text-success">in use</span>}
                    {!p.exists && <span className="ml-2 rounded bg-warning/15 px-1.5 text-caption text-warning">file missing</span>}</div>
                  <div className="mono truncate text-caption text-muted-foreground" title={p.path}>
                    {Math.round((p.profile.num_ctx || 0) / 1024)}k ctx · {p.profile.kv_cache || "auto"}{p.profile.n_cpu_moe !== undefined ? ` · ${p.profile.n_cpu_moe} CPU expert layers` : ""} · {p.path}</div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {name !== current && p.exists && <Button size="xs" variant="outline" onClick={() => onUse(name)}>Use</Button>}
                  {p.exists && <Button size="xs" variant="ghost" onClick={() => inspect(p.path)}>Re-tune</Button>}
                  <Button size="icon-sm" variant="ghost" aria-label="Remove profile" onClick={() => removeProfile(name)}><Trash2 /></Button>
                </div>
              </div>
            ))}
            {loose.map((f) => (
              <div key={f.path} className="flex items-center justify-between gap-2 rounded-lg border border-dashed border-line-1 px-3 py-2">
                <div className="min-w-0"><div className="text-ui">{f.name}</div>
                  <div className="text-caption text-muted-foreground">{gb(f.size)} · {f.architecture}{f.experts ? " · MoE" : ""} · not set up yet</div></div>
                <Button size="xs" variant="outline" onClick={() => inspect(f.path)}>Set up</Button>
              </div>
            ))}
            <div className="text-caption text-muted-foreground">
              Models folder: <span className="mono">{list.models_dir}</span>
              {list.gpu_mib ? ` · GPU ${(list.gpu_mib / 1024).toFixed(0)} GB` : " · no NVIDIA GPU (CPU only)"} · RAM {(list.ram_mib / 1024).toFixed(0)} GB
            </div>
          </div>
        )}
      </Section>

      {prop && <Section title="Set up a model"><ProposalEditor key={prop.info.path} p={prop} onUse={onUse} onDone={() => { setProp(null); load() }} /></Section>}

      <Section title="Add a model">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={pick} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <FileUp />} Pick a .gguf file</Button>
          <Button size="sm" variant="outline" onClick={() => help.open("models").catch((e: any) => toast.error(e.message))}><FolderOpen /> Open models folder</Button>
          <Button size="sm" variant="ghost" onClick={load}><RefreshCw /> Refresh</Button>
        </div>
        <Field label="Download" hint="Paste a link to a .gguf file, e.g. https://huggingface.co/<org>/<repo>/resolve/main/<file>.gguf (a Hugging Face file page link works too). Resumes if interrupted.">
          <div className="flex gap-1.5">
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://huggingface.co/…/model.gguf" className={inputCls + " mono text-caption"} />
            <Button size="sm" onClick={download} disabled={!url.trim()}><Download /> Download</Button>
          </div>
        </Field>
        {active.map((d) => (
          <div key={d.id} className="mt-1.5 rounded-md border border-line-1 px-2.5 py-1.5 text-caption">
            <div className="flex justify-between"><span className="mono truncate">{d.id}</span>
              <span className={d.status === "failed" ? "text-destructive" : "text-muted-foreground"}>
                {d.status === "failed" ? d.error : `${d.status} ${d.total ? Math.round((100 * d.done) / d.total) + "%" : ""} ${gb(d.done)}${d.total ? " / " + gb(d.total) : ""}`}</span></div>
            {d.total > 0 && <div className="mt-1 h-1 overflow-hidden rounded bg-surface-3"><div className="h-full bg-info" style={{ width: `${(100 * d.done) / d.total}%` }} /></div>}
            <div className="mt-1 flex gap-1.5">
              {d.status === "downloading" && <Button size="xs" variant="ghost" onClick={() => modelsApi.cancel(d.id)}>Pause</Button>}
              {(d.status === "paused" || d.status === "failed") && <Button size="xs" variant="ghost" onClick={() => modelsApi.download(d.url).then(pollDownloads)}>Resume</Button>}
            </div>
          </div>
        ))}
      </Section>
    </>
  )
}
