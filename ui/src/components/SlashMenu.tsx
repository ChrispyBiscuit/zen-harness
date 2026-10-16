import { useEffect, useRef } from "react"
import { cn } from "@/lib/utils"

export type SlashCommand = { name: string; hint: string; run: () => void }

/** The command list shown above the message box while you type "/…". Scrolls when it is long; the highlighted
 *  command stays in view while you move with the arrow keys. */
export function SlashMenu({ items, active, onPick }: { items: SlashCommand[]; active: number; onPick: (c: SlashCommand) => void }) {
  const list = useRef<HTMLDivElement>(null)
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" })
  }, [active])
  if (!items.length) return null
  return (
    <div className="absolute inset-x-2 bottom-full z-30 mb-2 flex max-h-[min(55vh,380px)] flex-col overflow-hidden rounded-panel border border-line-2 bg-surface-2 shadow-elev-2">
      <div className="label-tech border-b border-border/60 px-3 py-1.5 text-faint">Commands</div>
      <div ref={list} className="min-h-0 flex-1 overflow-y-auto py-1">
        {items.map((c, i) => (
          <button key={c.name} data-idx={i} onMouseDown={(e) => { e.preventDefault(); onPick(c) }}
            className={cn("flex w-full items-baseline gap-3 px-3 py-1.5 text-left transition-colors",
              i === active ? "bg-accent" : "hover:bg-accent/50")}>
            <span className={cn("mono w-28 shrink-0 text-ui", i === active ? "text-brand" : "text-foreground/90")}>/{c.name}</span>
            <span className="truncate text-caption text-muted-foreground">{c.hint}</span>
          </button>
        ))}
      </div>
      <div className="label-tech border-t border-border/60 px-3 py-1.5 text-faint">↑↓ choose · Enter / Tab run · Esc close</div>
    </div>
  )
}
