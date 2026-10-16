import { useEffect, useRef, useState } from "react"
import { BookOpen, Check, ChevronRight, Pencil, Search, Trash2, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Markdown } from "./Markdown"
import { cn } from "@/lib/utils"

type Note = { id: number; text: string; date?: string; auto?: boolean }
type Book = { name: string; title: string; triggers: string; examples: string[]; applies_when: string; body: string; enabled: boolean; notes: Note[] }
type Resp = { items: Book[]; min_sim: number; enabled: boolean }
type Match = { name: string; title: string; sim: number | null; fires: boolean }

const inputCls = "w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-ui outline-none focus:border-ring"

/** Task playbooks: short how-to-approach guides matched to each new request, with the lessons runs left behind. */
export function PlaybooksPanel() {
  const [data, setData] = useState<Resp | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [test, setTest] = useState("")
  const [matches, setMatches] = useState<Match[] | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const load = () => fetch("/api/playbooks").then((r) => r.json()).then(setData).catch(() => setData(null))
  useEffect(() => { load() }, [])
  useEffect(() => {
    window.clearTimeout(timer.current)
    if (!test.trim()) { setMatches(null); return }
    timer.current = window.setTimeout(() => {
      fetch(`/api/playbooks/match?q=${encodeURIComponent(test)}`).then((r) => r.json()).then((d) => setMatches(d.matches || [])).catch(() => setMatches([]))
    }, 300)
  }, [test])

  const toggle = async (b: Book) => {
    await fetch(`/api/playbooks/${b.name}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !b.enabled }) })
    load()
  }
  const save = async (name: string) => {
    const r = await fetch(`/api/playbooks/${name}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: draft }) })
    if (!r.ok) return toast.error("Could not save")
    setEditing(null); load()
  }
  const delNote = async (id: number) => { await fetch(`/api/playbooks/notes/${id}`, { method: "DELETE" }); load() }

  return (
    <div className="space-y-3 py-2">
      <p className="text-caption text-muted-foreground">
        At the start of each new task the request is matched (by meaning) against these playbooks; the best one is shown to the agent
        as a short guide: what done looks like, approach, tools, pitfalls, when to stop. Lessons the agent records with
        <code className="mono"> playbook_note</code> (and dead ends the app detects) appear under the playbook next time.
        {data && <> {data.items.length} playbooks · fires at similarity ≥ {data.min_sim}{data.enabled === false && <span className="text-warning"> · playbooks are turned off in Agent settings</span>}</>}
      </p>
      <div className="relative">
        <Search className="absolute left-2.5 top-2 size-4 text-muted-foreground" />
        <input value={test} onChange={(e) => setTest(e.target.value)} placeholder="Test: type a request to see which playbook would fire…" className={inputCls + " pl-8"} />
      </div>
      {matches && (
        <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-caption">
          {matches.length === 0 ? <span className="text-muted-foreground">No playbook is close enough — nothing would be injected.</span> : matches.map((m, i) => (
            <div key={m.name} className={cn("flex items-center gap-2", !m.fires && "text-muted-foreground")}>
              <span className={cn("w-12 shrink-0 text-right tabular-nums", m.fires ? "text-success" : "")}>{m.sim != null ? m.sim.toFixed(2) : "–"}</span>
              <span className="truncate">{m.title}</span>
              {i === 0 && m.fires && <span data-tone="success" className="tone-soft tone-text rounded-chip px-1.5 text-meta">would fire</span>}
            </div>
          ))}
        </div>
      )}
      <div className="space-y-1.5">
        {data?.items.map((b) => {
          const isOpen = open === b.name
          return (
            <div key={b.name} className={cn("rounded-lg border border-border bg-surface-1", !b.enabled && "opacity-60")}>
              <div className="flex items-center gap-2 px-3 py-2">
                <button onClick={() => setOpen(isOpen ? null : b.name)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  <ChevronRight className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-90")} />
                  <BookOpen className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate text-ui">{b.title}</span>
                  {b.notes.length > 0 && <span className="shrink-0 text-meta text-muted-foreground">{b.notes.length} note{b.notes.length === 1 ? "" : "s"}</span>}
                </button>
                <label className="flex shrink-0 items-center gap-1.5 text-caption text-muted-foreground">
                  <input type="checkbox" checked={b.enabled} onChange={() => toggle(b)} /> on
                </label>
              </div>
              {isOpen && (
                <div className="space-y-2 border-t border-border/60 px-3 py-2.5">
                  <div className="text-caption text-muted-foreground">
                    <span className="text-foreground/80">Applies when:</span> {b.applies_when}
                    <span className="mx-1.5">·</span><span className="text-foreground/80">Triggers:</span> {b.triggers}
                  </div>
                  {editing === b.name ? (
                    <div className="space-y-2">
                      <textarea autoFocus rows={14} value={draft} onChange={(e) => setDraft(e.target.value)} className={inputCls + " mono text-caption"} />
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}><X className="size-3.5" />Cancel</Button>
                        <Button size="sm" onClick={() => save(b.name)}><Check className="size-3.5" />Save</Button>
                      </div>
                    </div>
                  ) : (
                    <div className="relative">
                      <Markdown text={b.body} className="text-caption" />
                      <button title="Edit" onClick={() => { setEditing(b.name); setDraft(b.body) }}
                        className="absolute right-0 top-0 rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"><Pencil className="size-3.5" /></button>
                    </div>
                  )}
                  {b.notes.length > 0 && (
                    <div className="rounded-md bg-muted/30 px-2.5 py-2">
                      <div className="mb-1 text-meta uppercase tracking-wide text-faint">From earlier runs</div>
                      {b.notes.map((n) => (
                        <div key={n.id} className="group flex items-start gap-2 py-0.5 text-caption">
                          <span className="min-w-0 flex-1">{n.text} <span className="text-meta text-muted-foreground">{n.date}{n.auto ? " · detected by the app" : ""}</span></span>
                          <button title="Delete note" onClick={() => delNote(n.id)} className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"><Trash2 className="size-3.5" /></button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
