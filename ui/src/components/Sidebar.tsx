import { memo, useMemo, useState } from "react"
import { MoreHorizontal, Pencil, Search, SquarePen, Trash2, Workflow } from "lucide-react"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { api } from "@/lib/api"
import { deleteWithUndo } from "@/lib/undo"
import { useApp } from "@/lib/store"
import type { Thread } from "@/lib/types"
import { cn } from "@/lib/utils"
import { Schedules } from "./Schedules"

function bucket(ts: number) {
  const d = new Date(ts * 1000)
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t = d.getTime()
  if (t >= start) return "Today"
  if (t >= start - 86400000) return "Yesterday"
  if (t >= start - 6 * 86400000) return "Previous 7 days"
  if (t >= start - 29 * 86400000) return "Previous 30 days"
  return "Older"
}

const SIDEBAR_ROWS = 150

// memo: a streaming update replaces one chat's row; the other rows keep their objects and are not redrawn
const ThreadRow = memo(function ThreadRow({ t, child }: { t: Thread; child?: boolean }) {
  const current = useApp((s) => s.current)
  const select = useApp((s) => s.select)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(t.title)
  const active = current === t.id
  return (
    <div className={cn("group relative flex min-h-(--row-h) items-center rounded-lg transition-ui", active ? "bg-surface-2 shadow-(--hi)" : "hover:bg-surface-1 active:bg-surface-2", child && "ml-4",
      t.running && !t.waiting && "zen-refract")}>
      {editing ? (
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { setEditing(false); if (title.trim() && title !== t.title) api.rename(t.id, title.trim()) }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setTitle(t.title); setEditing(false) } }}
          className="m-1 w-full rounded bg-background px-2 py-1 text-ui outline-none ring-1 ring-ring" />
      ) : (
        <button onClick={() => select(t.id)} title={t.title.replace(/^⇢\s*/, "")} className="flex min-w-0 flex-1 items-center gap-2 self-stretch rounded-lg px-2 py-(--row-py) text-left">
          {child && <Workflow className="size-3.5 shrink-0 text-muted-foreground" />}
          <span className={cn("truncate text-ui", active ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/85")}>
            {t.title.replace(/^⇢\s*/, "")}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1 pl-1">
            {t.waiting && <span data-tone="warning" className="tone-fill size-2 rounded-full" title="Needs your approval" />}
            {t.running && !t.waiting && <span className="sr-only">running</span>}
          </span>
        </button>
      )}
      {!editing && (
        <DropdownMenu>
          <DropdownMenuTrigger render={
            <button aria-label="Chat options" className="mr-1 rounded p-1 text-muted-foreground opacity-0 hover:bg-surface-3 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100 data-[popup-open]:opacity-100" />}>
            <MoreHorizontal className="size-3.5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem onClick={() => setEditing(true)}><Pencil /> Rename</DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onClick={() => {
              const hidden = () => ((useApp.getState() as any).hiddenThreads || []) as string[]
              deleteWithUndo({
                label: `Deleted “${t.title.replace(/^⇢\s*/, "").slice(0, 60)}”`,
                hide: () => {
                  useApp.getState().set({ hiddenThreads: [...hidden(), t.id] } as any)
                  if (useApp.getState().current === t.id) select(null)
                },
                restore: () => useApp.getState().set({ hiddenThreads: hidden().filter((x) => x !== t.id) } as any),
                commit: () => api.remove(t.id),
              })
            }}><Trash2 /> Delete</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
})

export function Sidebar() {
  const allThreads = useApp((s) => s.threads)
  const hiddenThreads = useApp((s) => (s as any).hiddenThreads as string[] | undefined)
  const threads = useMemo(() => (hiddenThreads?.length ? allThreads.filter((t) => !hiddenThreads.includes(t.id)) : allThreads),
    [allThreads, hiddenThreads])
  const select = useApp((s) => s.select)
  const [q, setQ] = useState("")
  // 2,800 imported chats drew 2,800 rows: the newest SHOWN are drawn, "Show more" adds more; search sees all
  const [shown, setShown] = useState(SIDEBAR_ROWS)

  const groups = useMemo(() => {
    const ql = q.toLowerCase()
    const match = (t: Thread) => !ql || t.title.toLowerCase().includes(ql) || t.snippet.toLowerCase().includes(ql)
    const children: Record<string, Thread[]> = {}
    for (const t of threads) if (t.parent) (children[t.parent] ||= []).push(t)
    const tops = threads.filter((t) => !t.parent || !threads.some((p) => p.id === t.parent))
    const out: [string, { t: Thread; kids: Thread[] }[]][] = []
    for (const t of tops) {
      const kids = children[t.id] || []
      if (!match(t) && !kids.some(match)) continue
      const b = t.running || t.waiting ? "Active" : bucket(t.updated || 0)
      let g = out.find((x) => x[0] === b)
      if (!g) out.push((g = [b, []]))
      g[1].push({ t, kids })
    }
    const order = ["Active", "Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"]
    const sorted = out.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    const total = sorted.reduce((n, g) => n + g[1].length, 0)
    if (q || total <= shown) return { groups: sorted, hidden: 0 }
    let left = shown
    const cut: typeof sorted = []
    for (const [name, items] of sorted) {
      if (left <= 0) break
      cut.push([name, items.slice(0, left)])
      left -= items.length
    }
    return { groups: cut, hidden: total - shown }
  }, [threads, q, shown])

  return (
    <aside className="flex h-full w-[264px] shrink-0 flex-col bg-ground">
      {/* the name and ensō live in the title bar above; the sidebar starts with new chat, a quiet row that matches
          the chat list, not a boxed button */}
      <div className="px-2 pb-1 pt-2">
        <button onClick={() => select(null)} title="New chat (Ctrl+N)"
          className="group flex min-h-(--row-h) w-full items-center gap-2.5 rounded-lg px-2.5 py-(--row-py) text-left text-ui text-sidebar-foreground/90 transition-ui hover:bg-surface-1 active:bg-surface-2">
          <SquarePen className="size-4 text-muted-foreground transition-ui group-hover:text-foreground" />
          <span className="flex-1">New chat</span>
          <kbd className="mono text-meta text-faint opacity-0 transition-ui group-hover:opacity-100">Ctrl N</kbd>
        </button>
      </div>
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 rounded-lg bg-surface-1 px-2 py-1.5 focus-within:outline-2 focus-within:outline-(--focus-ring)">
          <Search className="size-3.5 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats"
            aria-label="Search chats" className="w-full bg-transparent text-ui outline-none focus-visible:outline-none placeholder:text-faint" />
        </div>
      </div>
      <div data-density-scope className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {groups.groups.map(([name, items]) => (
          <div key={name} className="mt-3">
            <div className="label-tech px-2 pb-1 text-faint">{name}</div>
            {items.map(({ t, kids }) => (
              <div key={t.id}>
                <ThreadRow t={t} />
                {kids.map((k) => <ThreadRow key={k.id} t={k} child />)}
              </div>
            ))}
          </div>
        ))}
        {groups.hidden > 0 && (
          <button onClick={() => setShown((n) => n + SIDEBAR_ROWS * 2)}
            className="mt-2 w-full rounded-lg px-2.5 py-1.5 text-left text-meta text-muted-foreground transition-ui hover:bg-surface-1 hover:text-foreground">
            Show more ({groups.hidden.toLocaleString()} older chats) · or search above
          </button>
        )}
      </div>
      {/* scheduled tasks at the bottom; its bottom row also holds the sidebar's fold button (bottom-right) */}
      <Schedules />
    </aside>
  )
}
