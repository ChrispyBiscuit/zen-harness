import { useEffect, useState } from "react"
import { BookOpen, ClipboardCopy, FolderOpen, Info, Scale } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Markdown } from "@/components/Markdown"
import { help, type About, type DocPage, type LicenseComponent } from "@/lib/api"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"

/* Help: the user documentation (docs/*.md, served by backend help_api.py) plus About and diagnostics.
   Opened from the ? button in the title bar, the /help command, or useApp.set({ helpOpen: true, helpPage }). */

function fmtMiB(m?: number | null) {
  return m ? (m >= 1024 ? `${(m / 1024).toFixed(1)} GB` : `${m} MB`) : "?"
}

function AboutPane() {
  const [a, setA] = useState<About | null>(null)
  useEffect(() => { help.about().then(setA).catch((e) => toast.error(e.message)) }, [])
  const copyDiag = async () => {
    try {
      const { text } = await help.diagnostics()
      await navigator.clipboard.writeText(text)
      toast.success("Diagnostics copied", { description: "Paste them into your bug report. Tokens and keys are removed." })
    } catch (e: any) { toast.error(e.message) }
  }
  if (!a) return <div className="p-4 text-muted-foreground">Loading…</div>
  const folder = (key: keyof About["folders"], label: string) => (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="min-w-0"><div className="text-ui">{label}</div><div className="mono truncate text-caption text-muted-foreground" title={a.folders[key]}>{a.folders[key]}</div></div>
      <Button size="xs" variant="outline" onClick={() => help.open(key).catch((e: any) => toast.error(e.message))}><FolderOpen /> Open</Button>
    </div>
  )
  return (
    <div className="space-y-4 p-1">
      <div>
        <div className="text-lg font-medium">{a.name} <span className="text-muted-foreground">{a.version}</span></div>
        <div className="text-caption text-muted-foreground">{a.frozen ? "Installed app" : "Development checkout"} · Python {a.python} · Windows {a.windows}</div>
      </div>
      <div className="rounded-lg border border-line-1 bg-surface-1 px-3 py-2 text-ui">
        <div>GPU: {a.gpu ? `${a.gpu.name} · ${fmtMiB(a.gpu.vram_mib)} video memory · driver ${a.gpu.driver}` : "no NVIDIA GPU found — models run on the CPU"}</div>
        <div>Memory: {fmtMiB(a.ram_mib)} RAM</div>
        <div>llama.cpp: {a.llama_cpp_found ? "found" : <span className="text-warning">not found — local models can't run</span>}</div>
      </div>
      <div>
        {folder("data", "Your data (settings, chats, memory)")}
        {folder("models", "Models")}
        {folder("logs", "Logs")}
        {folder("install", "Program")}
      </div>
      <Button variant="outline" onClick={copyDiag}><ClipboardCopy /> Copy diagnostics</Button>
    </div>
  )
}

const KIND_LABEL: Record<string, string> = {
  binary: "Programs and runtimes", model: "Models", python: "Python libraries", npm: "Interface libraries (JavaScript)",
  "model (your models folder)": "Models in your models folder",
}
const KIND_ORDER = ["binary", "model", "model (your models folder)", "python", "npm"]

function LicensesPane() {
  const [data, setData] = useState<Awaited<ReturnType<typeof help.licenses>> | null>(null)
  const [q, setQ] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const [text, setText] = useState("")
  useEffect(() => { help.licenses().then(setData).catch((e) => toast.error(e.message)) }, [])
  const show = async (c: LicenseComponent) => {
    if (open === c.id) { setOpen(null); return }
    setOpen(c.id); setText("")
    if (c.text) help.licenseText(c.text).then((r) => setText(r.text)).catch(() => setText("(text not available)"))
  }
  if (!data) return <div className="p-4 text-muted-foreground">Loading…</div>
  const ql = q.trim().toLowerCase()
  const comps = data.components.filter((c) => !ql || `${c.name} ${c.license} ${c.version}`.toLowerCase().includes(ql))
  const groups = [...new Set([...KIND_ORDER, ...comps.map((c) => c.kind)])].map((k) => [k, comps.filter((c) => c.kind === k)] as const).filter(([, l]) => l.length)
  return (
    <div className="space-y-4 p-1">
      <div>
        <div className="text-lg font-medium">{data.app.name} <span className="text-muted-foreground">{data.app.version}</span></div>
        <pre className="mt-2 whitespace-pre-wrap rounded-lg border border-line-1 bg-surface-1 p-3 text-caption leading-relaxed">{data.app.license}</pre>
      </div>
      {!data.generated && <div className="text-caption text-warning">The third-party list has not been generated in this checkout (run packaging/gen_licenses.py). Installed builds always include it.</div>}
      <div className="flex items-center justify-between gap-3">
        <div className="text-ui">Third-party software and models <span className="text-muted-foreground">({data.components.length})</span></div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or licence…"
          className="w-56 rounded-lg border border-input bg-transparent px-2.5 py-1 text-caption outline-none focus:border-ring" />
      </div>
      {groups.map(([kind, list]) => (
        <section key={kind}>
          <h3 className="label-tech mb-1 text-muted-foreground/70">{KIND_LABEL[kind] || kind} · {list.length}</h3>
          <div className="divide-y divide-line-1 rounded-lg border border-line-1">
            {list.map((c) => (
              <div key={c.id} className="px-3 py-1.5">
                <button onClick={() => show(c)} className="flex w-full items-baseline justify-between gap-3 text-left">
                  <span className="min-w-0 truncate text-ui">{c.name} <span className="text-muted-foreground">{c.version}</span></span>
                  <span className="shrink-0 text-caption text-muted-foreground">{c.license}</span>
                </button>
                {open === c.id && (
                  <div className="mt-1.5 space-y-1.5">
                    {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="block truncate text-caption text-info underline">{c.url}</a>}
                    {c.text ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-surface-1 p-2 text-[11px] leading-relaxed">{text || "Loading…"}</pre>
                      : <div className="text-caption text-muted-foreground">Licence: {c.license}. {c.url ? "Full terms at the link above." : ""}</div>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

export function HelpDialog() {
  const open = useApp((s) => (s as any).helpOpen) as boolean
  const want = useApp((s) => (s as any).helpPage) as string | undefined
  const set = useApp((s) => s.set)
  const [pages, setPages] = useState<DocPage[]>([])
  const [page, setPage] = useState<string>("getting-started")
  const [md, setMd] = useState<string>("")
  useEffect(() => {
    if (!open) return
    help.docs().then((d) => setPages(d.pages)).catch((e) => toast.error(e.message))
    if (want) setPage(want)
  }, [open, want])
  useEffect(() => {
    if (!open || page === "about" || page === "licenses") return
    setMd("")
    help.doc(page).then((d) => setMd(d.markdown)).catch(() => setMd("This page could not be loaded."))
  }, [open, page])
  // links between help pages ("models.md#x") stay inside the dialog; everything else behaves as usual
  const onClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a")
    const href = a?.getAttribute("href") || ""
    const m = href.match(/^(?:\.\/)?([a-z0-9-]+)\.md(?:#.*)?$/)
    if (m) { e.preventDefault(); setPage(m[1]) }
  }
  return (
    <Dialog open={!!open} onOpenChange={(v) => set({ helpOpen: v, ...(v ? {} : { helpPage: undefined }) } as any)}>
      <DialogContent className="flex h-[min(88dvh,820px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="shrink-0 border-b border-line-1 px-6 pb-3 pt-5">
          <DialogTitle className="flex items-center gap-2"><span className="label-tech text-muted-foreground/60">Zen Harness</span><span className="text-muted-foreground/30">/</span>Help</DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 flex-1">
          <nav className="w-56 shrink-0 space-y-0.5 overflow-y-auto border-r border-line-1 p-3">
            {pages.map((p) => (
              <button key={p.slug} onClick={() => setPage(p.slug)}
                className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-ui transition-ui",
                  page === p.slug ? "bg-surface-3 text-foreground" : "text-muted-foreground hover:text-foreground")}>
                <BookOpen className="size-3.5 shrink-0" /><span className="truncate">{p.title}</span>
              </button>
            ))}
            <button onClick={() => setPage("about")}
              className={cn("mt-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-ui transition-ui",
                page === "about" ? "bg-surface-3 text-foreground" : "text-muted-foreground hover:text-foreground")}>
              <Info className="size-3.5 shrink-0" /> About & diagnostics
            </button>
            <button onClick={() => setPage("licenses")}
              className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-ui transition-ui",
                page === "licenses" ? "bg-surface-3 text-foreground" : "text-muted-foreground hover:text-foreground")}>
              <Scale className="size-3.5 shrink-0" /> Licences
            </button>
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4" onClickCapture={onClick}>
            {page === "about" ? <AboutPane /> : page === "licenses" ? <LicensesPane /> : md ? <Markdown text={md} /> : <div className="text-muted-foreground">Loading…</div>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
