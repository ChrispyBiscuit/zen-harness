import { createContext, useContext } from "react"
import { cn } from "@/lib/utils"

/** True while the message holding the blocks is still being written (a block that doesn't parse yet is unfinished,
 *  not wrong). */
export const LiveContext = createContext(false)
export const useLive = () => useContext(LiveContext)

/** The card every block sits in: a title row (with optional actions on the right) and the body. */
export function Frame({ title, subtitle, actions, children, className, bodyClass }: {
  title?: string; subtitle?: string; actions?: React.ReactNode; children: React.ReactNode; className?: string; bodyClass?: string
}) {
  return (
    <div className={cn("answer-block not-prose my-3 overflow-hidden rounded-[var(--radius-panel)] bg-surface-1 ring-1 ring-line-1 shadow-[var(--hi)]", className)}>
      {(title || actions) && (
        <div className="flex min-h-9 items-center gap-2 px-3.5 pt-2.5">
          <div className="min-w-0 flex-1">
            {title && <div className="truncate text-ui font-semibold text-foreground">{title}</div>}
            {subtitle && <div className="truncate text-meta text-faint">{subtitle}</div>}
          </div>
          {actions}
        </div>
      )}
      <div className={cn("px-3.5 pb-3 pt-2", bodyClass)}>{children}</div>
    </div>
  )
}

/** While the block is still streaming: a quiet placeholder of about the right size. */
export function Pending({ kind, height = 160 }: { kind: string; height?: number }) {
  return (
    <div className="answer-block not-prose my-3 flex items-center justify-center rounded-[var(--radius-panel)] bg-surface-1 ring-1 ring-line-1"
      style={{ height }}>
      <div className="flex items-center gap-2 text-meta text-faint">
        <span className="size-1.5 animate-pulse rounded-full bg-faint" />
        Drawing {kind}…
      </div>
    </div>
  )
}

export function ToggleButton({ on, onClick, children, label }: { on?: boolean; onClick: () => void; children: React.ReactNode; label: string }) {
  return (
    <button onClick={onClick} aria-pressed={on} title={label} aria-label={label}
      className={cn("rounded-[var(--radius-chip)] px-2 py-0.5 text-meta transition-colors",
        on ? "bg-surface-3 text-foreground" : "text-faint hover:bg-surface-2 hover:text-foreground")}>
      {children}
    </button>
  )
}
