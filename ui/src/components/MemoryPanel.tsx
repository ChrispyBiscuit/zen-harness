import { useEffect, useRef, useState } from "react"
import { Check, Pencil, Plus, Search, Trash2, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { PlaybooksPanel } from "./PlaybooksPanel"
import { cn } from "@/lib/utils"

type Item = { id: number; kind: string; text: string; tags?: string; title?: string; role?: string; updated?: number; created?: number; sim?: number | null; matched?: string }
type Resp = { items: Item[]; counts: { memory: number; chat: number; files?: { files: number; chunks: number } }; embeddings: boolean; embed_error?: string | null }

const inputCls = "w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-ui outline-none focus:border-ring"

function age(ts?: number) {
  if (!ts) return ""
  const d = (Date.now() / 1000 - ts) / 86400
  return d < 1 ? "today" : d < 60 ? `${Math.floor(d)}d ago` : new Date(ts * 1000).toLocaleDateString()
}

/** Long-term memory: what the agent has saved, plus hybrid search over memories and past chats. */
export function MemoryPanel() {
  const [view, setView] = useState<"memories" | "playbooks">("memories")
  const tab = (v: typeof view, label: string) => (
    <button onClick={() => setView(v)} className={cn("rounded-md px-2.5 py-1 text-caption", view === v ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-surface-2")}>{label}</button>
  )
  const switcher = <div className="flex gap-1">{tab("memories", "Memories")}{tab("playbooks", "Playbooks")}</div>
  if (view === "playbooks") return <div>{switcher}<PlaybooksPanel /></div>
  return <div>{switcher}<MemoriesView /></div>
}

function MemoriesView() {
  const [q, setQ] = useState("")
  const [scope, setScope] = useState<"memory" | "all">("memory")
  const [data, setData] = useState<Resp | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState("")
  const [adding, setAdding] = useState("")
  const timer = useRef<number | undefined>(undefined)

  const load = (query = q, sc = scope) => {
    const p = new URLSearchParams({ q: query, scope: sc })
    fetch(`/api/memory?${p}`).then((r) => r.json()).then(setData).catch(() => setData(null))
  }
  useEffect(() => { load() }, [])
  useEffect(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => load(q, scope), 250)
  }, [q, scope])

  const save = async (id: number) => {
    const r = await fetch(`/api/memory/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: draft }) })
    if (!r.ok) return toast.error("Could not save")
    setEditing(null); load()
  }
  const del = async (id: number) => {
    await fetch(`/api/memory/${id}`, { method: "DELETE" }); load()
  }
  const add = async () => {
    if (!adding.trim()) return
    await fetch("/api/memory", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: adding }) })
    setAdding(""); load()
  }

  return (
    <div className="space-y-3 py-2">
      <p className="text-caption text-muted-foreground">
        Facts the agent saved with <code className="mono">memory_save</code> are listed in its prompt in every chat. Past conversations are
        indexed automatically so it can search them. Search here uses the same hybrid search: keywords + meaning.
        {data?.counts.files && <> Files in your allowed folders: {data.counts.files.files.toLocaleString()} indexed for search_documents (indexing runs while the agent is idle).</>}
        {data && <> {data.counts.memory} memor{data.counts.memory === 1 ? "y" : "ies"} · {data.counts.chat} conversation snippets indexed
          {!data.embeddings && <span className="text-warning"> · meaning search loading{data.embed_error ? ` (unavailable: ${data.embed_error.slice(0, 80)})` : "…"}</span>}</>}
      </p>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2 size-4 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories…" className={inputCls + " pl-8"} />
        </div>
        <select value={scope} onChange={(e) => setScope(e.target.value as "memory" | "all")} className={inputCls.replace("w-full", "w-44")}>
          <option value="memory">Saved memories</option>
          <option value="all">Memories + past chats</option>
        </select>
      </div>
      <div className="flex gap-2">
        <input value={adding} onChange={(e) => setAdding(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="Add a memory, e.g. “I prefer metric units”" className={inputCls} />
        <Button size="sm" variant="outline" onClick={add}><Plus className="size-3.5" />Add</Button>
      </div>
      <div className="space-y-1.5">
        {data?.items.length === 0 && <p className="py-6 text-center text-ui text-muted-foreground">{q ? "Nothing found." : "No memories yet."}</p>}
        {data?.items.map((it) => (
          <div key={it.id} className="group rounded-lg border border-border bg-surface-1 px-3 py-2">
            {editing === it.id ? (
              <div className="space-y-2">
                <textarea autoFocus rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} className={inputCls + " text-ui"} />
                <div className="flex justify-end gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(null)}><X className="size-3.5" />Cancel</Button>
                  <Button size="sm" onClick={() => save(it.id)}><Check className="size-3.5" />Save</Button>
                </div>
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-ui text-foreground/90">{it.kind === "chat" ? it.text.replace(/\s+/g, " ").slice(0, 400) : it.text}</div>
                  <div className="mt-0.5 text-meta text-muted-foreground">
                    {it.kind === "memory" ? `m${it.id}` : `chat “${(it.title || "").slice(0, 50)}” · ${it.role}`} · {age(it.kind === "memory" ? it.updated : it.created)}
                    {it.tags ? ` · ${it.tags}` : ""}{it.matched ? ` · matched by ${it.matched.replace("kw", "keywords").replace("vec", "meaning")}` : ""}
                  </div>
                </div>
                {it.kind === "memory" && (
                  <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button title="Edit" onClick={() => { setEditing(it.id); setDraft(it.text) }} className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"><Pencil className="size-3.5" /></button>
                    <button title="Delete" onClick={() => del(it.id)} className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-destructive"><Trash2 className="size-3.5" /></button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
