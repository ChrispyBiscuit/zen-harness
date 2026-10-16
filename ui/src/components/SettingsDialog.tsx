import { useEffect, useState } from "react"
import { ChevronRight, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { setDensity, storedDensity, type Density } from "@/lib/density"
import { setTextFx, storedTextFx, TEXT_FX } from "@/lib/textfx"
import { cn } from "@/lib/utils"
import { useApp } from "@/lib/store"
import type { Settings } from "@/lib/types"
import { MemoryPanel } from "@/components/MemoryPanel"
import { VoicePanel } from "@/components/VoicePanel"
import { HEADLESS_DEFAULTS, ServicePanel, type Headless } from "@/components/ServicePanel"
import { ModelsPanel } from "@/components/ModelsPanel"

/* Settings, kept small on purpose: what you actually change. Per-chat run controls (thinking, plan budget) live on
   the message box; per-model tuning (drafter, cache, GPU split) lives in the model's profile; retired experiments
   (hybrid executor, fan-out workers, interpret-first) are no longer shown — their saved values are left as they are. */

const CTX_PRESETS = [16384, 32768, 65536, 98304, 131072, 163840, 196608, 262144]
type Fit = { ctx: number; kv: string; max: Record<string, number>; note: string; total_mib?: number }
const OLD_TABS: Record<string, string> = { agent: "general", instructions: "agent", deleg: "agent", chat: "chatvoice",
  voice: "chatvoice", mcp: "extensions", appearance: "general", headless: "service", connect: "service" }

export const inputCls = "w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-ui outline-none focus:border-ring"

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="py-3 first:pt-1">
      <h3 className="label-tech mb-1 text-faint">{title}</h3>
      {children}
    </section>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[170px_1fr] items-start gap-4 py-2">
      <div className="pt-1.5 text-ui font-medium">{label}</div>
      <div>{children}{hint && <div className="mt-1.5 text-caption leading-relaxed text-muted-foreground">{hint}</div>}</div>
    </div>
  )
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 py-2">
      <Switch checked={checked} onCheckedChange={(v) => onChange(!!v)} className="mt-0.5" />
      <span><span className="text-ui">{label}</span>{hint && <span className="block text-caption text-muted-foreground">{hint}</span>}</span>
    </label>
  )
}

/** Fine-tuning that most people never touch: folded away. */
export function More({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1.5">
      <button type="button" onClick={() => setOpen(!open)} className="flex items-center gap-1 text-caption text-muted-foreground hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} /> Advanced
      </button>
      {open && <div className="mt-2 space-y-2">{children}</div>}
    </div>
  )
}

/** One list for every model: tuned profiles, downloaded files, Ollama models and — with a key — OpenAI models.
 *  Picking one sets the backend that runs it. */
function ModelPicker({ value, backend, models, openai, profiles, onChange }: {
  value: string; backend: string; models: string[]; openai: string[]; profiles: string[]
  onChange: (model: string, backend: string) => void
}) {
  const files = models.filter((m) => /\.gguf$/i.test(m))
  const tuned = models.filter((m) => profiles.includes(m) && !/\.gguf$/i.test(m))
  const ollama = models.filter((m) => !/\.gguf$/i.test(m) && !m.endsWith("-rag") && !tuned.includes(m))
  const sel = backend === "openai" ? `openai:${value}` : value
  const known = [...files, ...tuned, ...ollama].includes(value) || (backend === "openai" && openai.includes(value))
  const [custom, setCustom] = useState(!known && !!value)
  const label = (m: string) => /\.gguf$/i.test(m) ? m.split(/[\\/]/).pop()!.replace(/\.gguf$/i, "") : m.replace(/^hf\.co\//, "")
  return (
    <div className="space-y-1.5">
      <select value={custom ? "__custom" : sel} onChange={(e) => {
        const v = e.target.value
        if (v === "__custom") { setCustom(true); return }
        setCustom(false)
        if (v.startsWith("openai:")) onChange(v.slice(7), "openai")
        else onChange(v, "llama")
      }} className={inputCls}>
        {!value && !custom && <option value="">Choose a model…</option>}
        {!known && !custom && value && <option value={sel}>{label(value)}</option>}
        {tuned.length > 0 && <optgroup label="Tuned profiles (recommended)">{tuned.map((m) => <option key={m} value={m}>{m}</option>)}</optgroup>}
        {files.length > 0 && <optgroup label="Model files (models folder)">{files.map((m) => <option key={m} value={m}>{label(m)}</option>)}</optgroup>}
        {ollama.length > 0 && <optgroup label="Ollama models">{ollama.map((m) => <option key={m} value={m}>{label(m)}</option>)}</optgroup>}
        {openai.length > 0 && <optgroup label="OpenAI (cloud, billed per request)">{openai.map((m) => <option key={m} value={`openai:${m}`}>{m}</option>)}</optgroup>}
        <option value="__custom">Other (type a model name or .gguf path)…</option>
      </select>
      {custom && <input autoFocus value={value} onChange={(e) => onChange(e.target.value, /^(gpt-|o\d)/.test(e.target.value) ? "openai" : "llama")}
        placeholder="C:\\path\\to\\model.gguf or a model name" className={inputCls + " mono text-caption"} />}
    </div>
  )
}

const STYLES: Record<string, { label: string; hint: string; v: Record<string, number> }> = {
  precise: { label: "Precise — best for tools and code", hint: "Reliable tool calls and commands.", v: { temperature: 0.6, top_p: 0.95, top_k: 20, min_p: 0, presence_penalty: 0 } },
  balanced: { label: "Balanced — research and writing", hint: "More varied wording; discourages repetition.", v: { temperature: 1.0, top_p: 0.95, top_k: 20, min_p: 0, presence_penalty: 1.5 } },
  focused: { label: "Focused — most predictable", hint: "Very consistent, but small models can loop.", v: { temperature: 0.3, top_p: 0.9, top_k: 20, min_p: 0, presence_penalty: 0 } },
}

function ResponseStyle({ a, setA }: { a: Record<string, any>; setA: (p: Record<string, any>) => void }) {
  const cur = { temperature: a.temperature ?? 0.6, top_p: a.top_p ?? 0.95, top_k: a.top_k ?? 20, min_p: a.min_p ?? 0, presence_penalty: a.presence_penalty ?? 0 }
  const match = Object.entries(STYLES).find(([, p]) => Object.entries(p.v).every(([k, v]) => Number((cur as any)[k]) === v))?.[0] || "custom"
  const num = (k: string, step: number, min: number, max: number) => (
    <label className="flex items-center gap-1.5 text-caption text-muted-foreground">
      {k.replace("_", " ")}
      <input type="number" step={step} min={min} max={max} value={(cur as any)[k]} onChange={(e) => setA({ [k]: Number(e.target.value) })} className={inputCls.replace("w-full", "w-20")} />
    </label>
  )
  return (
    <Field label="Response style" hint={match === "custom" ? "Custom sampling values." : STYLES[match].hint}>
      <select value={match} onChange={(e) => e.target.value !== "custom" && setA({ ...STYLES[e.target.value].v })} className={inputCls}>
        {Object.entries(STYLES).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
        <option value="custom">Custom</option>
      </select>
      <More>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {num("temperature", 0.05, 0, 2)}{num("top_p", 0.05, 0, 1)}{num("top_k", 1, 0, 200)}{num("min_p", 0.01, 0, 1)}{num("presence_penalty", 0.1, 0, 2)}
        </div>
      </More>
    </Field>
  )
}

export function SettingsDialog() {
  const open = useApp((s) => s.settingsOpen)
  const startTab = useApp((s) => (s as any).settingsTab) as string | undefined
  const set = useApp((s) => s.set)
  const settings = useApp((s) => s.settings)
  const models = useApp((s) => s.models)
  const openai = useApp((s) => s.openai)
  const openaiModels = useApp((s) => s.openaiModels)
  const saveSettings = useApp((s) => s.saveSettings)
  const [draft, setDraft] = useState<Settings | null>(null)
  const [fit, setFit] = useState<Fit | null>(null)
  useEffect(() => { if (open && settings) setDraft(JSON.parse(JSON.stringify(settings))) }, [open])
  const fitKey = draft ? `${draft.agent.backend}|${draft.agent.model}|${draft.agent.num_ctx}|${draft.agent.kv_cache || "auto"}` : ""
  useEffect(() => {
    if (!draft || draft.agent.backend !== "llama") { setFit(null); return }
    const q = new URLSearchParams({ model: draft.agent.model, ctx: String(draft.agent.num_ctx), kv: draft.agent.kv_cache || "auto", fan: "0" })
    fetch(`/api/fit?${q}`).then((r) => r.json()).then(setFit).catch(() => setFit(null))
  }, [fitKey])
  if (!draft) return null
  const a = draft.agent
  const setA = (patch: Record<string, any>) => setDraft({ ...draft, agent: { ...a, ...patch } })
  const setUi = (patch: Record<string, any>) => setDraft({ ...draft, ui: { ...draft.ui, ...patch } as any })
  const headless: Headless = { ...HEADLESS_DEFAULTS, ...(draft.headless || {}) }
  const setH = (patch: Partial<Headless>) => setDraft({ ...draft, headless: { ...headless, ...patch } })
  const rules: { tool: string; pattern: string }[] = a.allow_rules || []
  const mcp: any[] = a.mcp_servers || []
  const profiles = Object.keys((a as any).model_overrides || {})
  const profile = (a as any).model_overrides?.[a.model]

  const save = async () => {
    try {
      // model profiles are saved by the Models tab itself (models_api); sending the dialog's copy back would
      // drop profiles added while it was open
      const { model_overrides: _profiles, ...agent } = draft.agent as Record<string, any>
      await saveSettings({ agent: agent as Settings["agent"], temperature: draft.temperature, num_ctx: draft.num_ctx,
        max_steps: draft.max_steps, system_prompt: draft.system_prompt, ui: draft.ui,
        ...(draft.headless ? { headless: draft.headless } : {}) })
      toast.success("Settings saved", { description: "Changes apply from the next message." })
      set({ settingsOpen: false })
    } catch (e: any) {
      toast.error(e.message)
    }
  }

  const fitHint = a.backend === "openai" ? "Cloud models manage their own context." : fit ? (
    <>
      {fit.note ? <span className="text-warning">{fit.note}</span>
        : <span className="text-success">Fits: {Math.round(fit.ctx / 1024)}k tokens, {fit.kv} cache{(fit as any).moe ? `, experts of ${(fit as any).experts_gpu_layers}/${(fit as any).layers} layers on the GPU` : ""}.</span>}
      {" "}Long chats are compressed automatically before they overflow.
    </>) : "Long chats are compressed automatically before they overflow."

  const tab = OLD_TABS[startTab || ""] || startTab || "general"
  return (
    <Dialog open={open} onOpenChange={(v) => set({ settingsOpen: v, ...(v ? {} : { settingsTab: undefined }) } as any)}>
      <DialogContent className="flex max-h-[min(88dvh,820px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl">
        <DialogHeader className="shrink-0 px-6 pb-3 pt-5"><DialogTitle className="flex items-center gap-2"><span className="label-tech text-faint">Zazen</span><span className="text-muted-foreground/30">/</span>Settings</DialogTitle></DialogHeader>
        <Tabs key={String(open) + tab} defaultValue={tab} className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-6 max-w-[calc(100%-3rem)] shrink-0 justify-start overflow-x-auto">
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="models">Models</TabsTrigger>
            <TabsTrigger value="agent">Agent</TabsTrigger>
            <TabsTrigger value="perms">Permissions</TabsTrigger>
            <TabsTrigger value="memory">Memory</TabsTrigger>
            <TabsTrigger value="chatvoice">Chat & voice</TabsTrigger>
            <TabsTrigger value="extensions">Extensions</TabsTrigger>
            <TabsTrigger value="service">Connect apps</TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-3">

            <TabsContent value="general">
              <Section title="Model">
                <Field label="Agent model" hint={a.backend === "openai" ? (openai ? "Runs on OpenAI with your key: no GPU, billed per request." : "No OpenAI key found (openai_key.txt).")
                  : profile ? "A tuned profile: its own context, cache and speed settings." : undefined}>
                  <ModelPicker value={a.model} backend={a.backend} models={models} openai={openai ? openaiModels : []} profiles={profiles}
                    onChange={(model, backend) => setA({ model, backend })} />
                </Field>
                {a.backend !== "openai" && profile && (
                  <Field label="Context window" hint="Set by the profile, with its cache and GPU settings. Long chats are compressed automatically before they overflow.">
                    <div className="pt-1.5 text-ui">{Math.round((profile.num_ctx || a.num_ctx) / 1024)}k tokens
                      <span className="text-muted-foreground">{profile.kv_cache ? ` · ${profile.kv_cache}${profile.kv_cache_v && profile.kv_cache_v !== profile.kv_cache ? "/" + profile.kv_cache_v : ""} cache` : ""}</span></div>
                  </Field>
                )}
                {a.backend !== "openai" && !profile && (
                  <Field label="Context window" hint={fitHint}>
                    <select value={CTX_PRESETS.includes(Number(a.num_ctx)) ? a.num_ctx : "custom"}
                      onChange={(e) => e.target.value !== "custom" && setA({ num_ctx: Number(e.target.value) })} className={inputCls}>
                      {CTX_PRESETS.map((n) => <option key={n} value={n}>{n / 1024}k tokens</option>)}
                      {!CTX_PRESETS.includes(Number(a.num_ctx)) && <option value="custom">{a.num_ctx} tokens</option>}
                    </select>
                    <More>
                      <select value={a.kv_cache || "auto"} onChange={(e) => setA({ kv_cache: e.target.value })} className={inputCls}>
                        <option value="auto">Cache: automatic</option><option value="f16">Cache: full precision (least context)</option>
                        <option value="q8_0">Cache: 8-bit (balanced)</option><option value="q4_0">Cache: 4-bit (most context, slight recall loss)</option>
                      </select>
                    </More>
                  </Field>
                )}
                <ResponseStyle a={a} setA={setA} />
                <Toggle checked={!!a.vision} onChange={(v) => setA({ vision: v })} label="Vision" hint="Send screenshots and attached images to the model (when it can see)." />
                <Toggle checked={(a as any).model_explanations !== false} onChange={(v) => setA({ model_explanations: v } as any)} label="Model explanations"
                  hint="The model writes a short line between steps saying what it is doing and why. Off: it works silently and you see only the tool steps and the answer." />
                {a.backend !== "openai" && (
                  <Toggle checked={!!(a as any).mtp_head} onChange={(v) => setA({ mtp_head: v } as any)} label="MTP head (faster generation)"
                    hint="Uses the model's built-in multi-token-prediction head to draft tokens ahead: typically 10-25% faster on code, less on prose. Costs about 0.6-1 GB of GPU memory, so on an 8 GB card the context shrinks by roughly 25-35K tokens. Only model files that include an MTP head use it; others ignore the switch." />
                )}
              </Section>
              <Section title="App">
                <Field label="Density">
                  <div role="radiogroup" aria-label="Density" className="inline-flex rounded-lg border border-line-1 bg-surface-1 p-0.5">
                    {(["comfortable", "compact"] as Density[]).map((d) => {
                      const on = (draft.ui as any).density === d || (!(draft.ui as any).density && d === storedDensity())
                      return (
                        <button key={d} role="radio" aria-checked={on} onClick={() => { setUi({ density: d }); setDensity(d) }}
                          className={cn("rounded-md px-3 py-1 text-ui capitalize transition-ui",
                            on ? "bg-surface-3 text-foreground shadow-elev-2" : "text-muted-foreground hover:text-foreground")}>{d}</button>
                      )
                    })}
                  </div>
                </Field>
                <Field label="Text effects" hint="How a reply looks while it streams in: glyphs decode into words with a chromatic split, and each paragraph glitches once as it is finished. Full is louder; Calm keeps the decode without the glitch; Off shows a plain cursor. Reduced-motion settings always calm it.">
                  <div role="radiogroup" aria-label="Text effects" className="inline-flex rounded-lg border border-line-1 bg-surface-1 p-0.5">
                    {TEXT_FX.map((v) => {
                      const on = (draft.ui as any).text_fx === v || (!(draft.ui as any).text_fx && v === storedTextFx())
                      return (
                        <button key={v} role="radio" aria-checked={on} onClick={() => { setUi({ text_fx: v } as any); setTextFx(v) }}
                          className={cn("rounded-md px-3 py-1 text-ui capitalize transition-ui",
                            on ? "bg-surface-3 text-foreground shadow-elev-2" : "text-muted-foreground hover:text-foreground")}>{v}</button>
                      )
                    })}
                  </div>
                </Field>
                <Toggle checked={draft.ui.notifications !== false} onChange={(v) => setUi({ notifications: v })}
                  label="Desktop notifications" hint="When a run finishes or needs your approval while the app is in the background." />
                <Toggle checked={(draft.ui as any).clip !== false} onChange={(v) => setUi({ clip: v } as any)}
                  label="Clip the paperclip" hint="A little assistant in the corner with a line of philosophy now and then, and query ideas." />
              </Section>
            </TabsContent>

            <TabsContent value="models">
              <ModelsPanel current={a.backend === "llama" ? a.model : ""} onUse={(name) => {
                setA({ model: name, backend: "llama" })
                toast.message(`“${name}” selected`, { description: "Click Save to use it from the next message." })
              }} />
            </TabsContent>

            <TabsContent value="agent">
              <Section title="Your instructions">
                <textarea rows={10} value={a.custom_instructions || ""} onChange={(e) => setA({ custom_instructions: e.target.value })}
                  placeholder={"How you want it to work, write and behave, e.g.\n- Be concise; lead with the answer.\n- Prefer primary sources and cite them."}
                  className={inputCls + " mono min-h-[180px] text-caption leading-relaxed"} />
                <div className="mt-1 flex justify-between text-caption text-muted-foreground">
                  <span>Added to every agent chat; takes priority over the defaults (safety rules still apply).</span>
                  <span>~{Math.ceil((a.custom_instructions || "").length / 4).toLocaleString()} tokens</span>
                </div>
              </Section>
              <Section title="How it works">
                <Toggle checked={a.interpret_first !== false} onChange={(v) => setA({ interpret_first: v })}
                  label="Understand each request first"
                  hint="Before working, the agent writes a short reading of your message — goal, your constraints, every condition the answer must meet, real ambiguities, anything irreversible — shown as an “Understood as” card. Adds about 1–3 s per request." />
                <Toggle checked={a.lean_first !== false} onChange={(v) => setA({ lean_first: v })}
                  label="Answer simple questions directly"
                  hint="Questions it can answer well are answered in seconds; anything that needs tools or work loads the full agent." />
                <Toggle checked={!a.compact_prompt} onChange={(v) => { if (v) setA({ compact_prompt: false }) }}
                  label="Legacy compaction"
                  hint="Use the previous, larger checkpoint policy. Off uses Zazen's default evidence-linked compact policy; full older records remain retrievable." />
                <Toggle checked={!!a.execution_memory_test_mode}
                  onChange={(v) => setA({ execution_memory_test_mode: v })}
                  label="Test Mode (experimental)"
                  hint="Keep a durable action journal and show a small, evidence-linked current-state view with recent actions and the next step. Works with either compaction setting." />
                <Toggle checked={a.delegation !== false} onChange={(v) => setA({ delegation: v })}
                  label="Allow sub-tasks" hint="The agent may hand independent parts of a task to background threads (shown in the Tasks panel)." />
                <Toggle checked={!!a.advisor?.enabled} onChange={(v) => setA({ advisor: { model: "gpt-6.1-sol", ...a.advisor, enabled: v } })}
                  label="Ask a cloud advisor when stuck"
                  hint={openai ? "Sends a summary of the chat to an OpenAI model; you approve each request." : "Needs an OpenAI key (openai_key.txt)."} />
                {a.advisor?.enabled && (
                  <Field label="Advisor model">
                    <select value={a.advisor?.model || "gpt-6.1-sol"} onChange={(e) => setA({ advisor: { ...a.advisor, model: e.target.value } })} className={inputCls}>
                      <option value="gpt-6.1-sol">gpt-6.1-sol (mid tier)</option>
                      <option value="gpt-6-astra">gpt-6-astra (frontier)</option>
                      <option value="gpt-6-luna">gpt-6-luna (small, cheapest)</option>
                    </select>
                  </Field>
                )}
                <Field label="Max steps per message" hint="It pauses and asks to continue after this many steps.">
                  <input type="number" min={1} max={300} value={a.max_steps} onChange={(e) => setA({ max_steps: Number(e.target.value) })} className={inputCls + " w-28"} />
                </Field>
              </Section>
              <p className="pt-1 text-caption text-muted-foreground">Effort (the cap on how hard it may think) is on the message box; how much to plan and think per request, the agent decides.</p>
            </TabsContent>

            <TabsContent value="perms">
              <Section title="Folders">
                <Field label="Allowed folders" hint="File tools work only inside these (attachments are always allowed). One per line.">
                  <textarea rows={3} value={(a.roots || []).join("\n")} onChange={(e) => setA({ roots: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} className={inputCls + " mono text-caption"} />
                </Field>
                <Toggle checked={a.file_index !== false} onChange={(v) => setA({ file_index: v })} label="Index them for search by content" hint="Runs while the agent is idle; only changed files are re-read." />
              </Section>
              <Section title="Without asking, it may">
                <Toggle checked={a.auto_read !== false} onChange={(v) => setA({ auto_read: v })} label="Read files" />
                <Toggle checked={a.auto_net !== false} onChange={(v) => setA({ auto_net: v })} label="Search and read the web" />
                <Toggle checked={a.auto_safe_cmds !== false} onChange={(v) => setA({ auto_safe_cmds: v })} label="Run read-only commands" hint="Get-*, git status/log/diff, dir… Anything that changes something still asks." />
              </Section>
              <Section title="Limits">
                <Toggle checked={!!a.plan_mode} onChange={(v) => setA({ plan_mode: v })} label="Read-only mode" hint="It can only investigate and propose; nothing is changed." />
                <Toggle checked={!!a.screen_control} onChange={(v) => setA({ screen_control: v })} label="Screen, mouse and keyboard control" hint="Off: it must ask first. It never types into sign-in or payment windows." />
                <Field label="Always allowed" hint="Added when you click “Always allow” on an approval. * is a wildcard.">
                  <div className="space-y-1.5">
                    {rules.length === 0 && <div className="text-caption text-muted-foreground">None yet.</div>}
                    {rules.map((r, i) => (
                      <div key={i} className="flex gap-1.5">
                        <input value={r.tool} onChange={(e) => setA({ allow_rules: rules.map((x, j) => j === i ? { ...x, tool: e.target.value } : x) })} className={inputCls + " mono w-32 text-caption"} />
                        <input value={r.pattern} onChange={(e) => setA({ allow_rules: rules.map((x, j) => j === i ? { ...x, pattern: e.target.value } : x) })} className={inputCls + " mono text-caption"} />
                        <Button size="icon-sm" variant="ghost" onClick={() => setA({ allow_rules: rules.filter((_, j) => j !== i) })} aria-label="Remove"><Trash2 /></Button>
                      </div>
                    ))}
                    <Button size="xs" variant="outline" onClick={() => setA({ allow_rules: [...rules, { tool: "powershell", pattern: "git *" }] })}><Plus /> Add rule</Button>
                  </div>
                </Field>
              </Section>
            </TabsContent>

            <TabsContent value="memory"><MemoryPanel /></TabsContent>

            <TabsContent value="chatvoice">
              <Section title="Chat mode">
                <Field label="Model">
                  <select value={draft.ui.chat_model || models[0]} onChange={(e) => setUi({ chat_model: e.target.value })} className={inputCls}>
                    {models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Field>
                <Toggle checked={!!draft.ui.think} onChange={(v) => setUi({ think: v })} label="Thinking" />
                <Field label="System prompt"><textarea rows={4} value={draft.system_prompt} onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })} className={inputCls + " text-caption"} /></Field>
                <More>
                  <div className="flex flex-wrap gap-4">
                    <label className="flex items-center gap-1.5 text-caption text-muted-foreground">temperature
                      <input type="number" step={0.1} value={draft.temperature} onChange={(e) => setDraft({ ...draft, temperature: Number(e.target.value) })} className={inputCls.replace("w-full", "w-20")} /></label>
                    <label className="flex items-center gap-1.5 text-caption text-muted-foreground">context
                      <input type="number" step={1024} value={draft.num_ctx} onChange={(e) => setDraft({ ...draft, num_ctx: Number(e.target.value) })} className={inputCls.replace("w-full", "w-28")} /></label>
                  </div>
                </More>
              </Section>
              <Section title="Voice">
                <VoicePanel ui={draft.ui as any} setUi={(p) => setUi(p)} />
              </Section>
            </TabsContent>

            <TabsContent value="extensions">
              <Section title="MCP servers">
                <div className="mb-2 text-caption text-muted-foreground">Extra tools from MCP servers. Command servers start with the first agent message.</div>
                <div className="space-y-2">
                  {mcp.map((s, i) => {
                    const upd = (p: any) => setA({ mcp_servers: mcp.map((x, j) => j === i ? { ...x, ...p } : x) })
                    return (
                      <div key={i} className="grid grid-cols-[auto_110px_1fr_1fr_130px_auto] items-center gap-1.5">
                        <Switch checked={s.enabled !== false} onCheckedChange={(v) => upd({ enabled: !!v })} />
                        <input placeholder="name" value={s.name || ""} onChange={(e) => upd({ name: e.target.value })} className={inputCls} />
                        <input placeholder="command or URL" value={s.url || s.command || ""} onChange={(e) => {
                          const v = e.target.value
                          upd(/^https?:/.test(v) ? { transport: "http", url: v, command: undefined } : { transport: "stdio", command: v, url: undefined })
                        }} className={inputCls + " mono text-caption"} />
                        <input placeholder="arguments" value={(s.args || []).join(" ")} onChange={(e) => upd({ args: e.target.value.split(" ").filter(Boolean) })} className={inputCls + " mono text-caption"} />
                        <select value={s.policy || "ask"} onChange={(e) => upd({ policy: e.target.value })} className={inputCls}>
                          <option value="ask">Ask each time</option><option value="read">Allow read-only</option><option value="allow">Allow all</option>
                        </select>
                        <Button size="icon-sm" variant="ghost" onClick={() => setA({ mcp_servers: mcp.filter((_, j) => j !== i) })} aria-label="Remove"><Trash2 /></Button>
                      </div>
                    )
                  })}
                  <Button size="xs" variant="outline" onClick={() => setA({ mcp_servers: [...mcp, { name: "", enabled: true, policy: "ask", transport: "stdio", command: "", args: [] }] })}><Plus /> Add server</Button>
                </div>
              </Section>
            </TabsContent>

            <TabsContent value="service"><ServicePanel h={headless} setH={setH} /></TabsContent>
          </div>
        </Tabs>
        <DialogFooter className="mx-0 mb-0 shrink-0 rounded-b-modal border-t border-line-1 bg-surface-3 px-6 py-3">
          <Button variant="ghost" onClick={() => set({ settingsOpen: false })}>Cancel</Button>
          <Button onClick={save}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
