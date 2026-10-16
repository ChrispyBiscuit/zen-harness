import { useEffect, useState } from "react"
import { FileText, Globe, Layers, ListChecks, PanelRight, PanelRightClose, PanelRightOpen, SquareTerminal, X } from "lucide-react"
import type { IDockviewHeaderActionsProps } from "dockview-react"
import { artifactsIn, filesIn } from "@/components/ArtifactsPanel"
import { latestChecklist, progress } from "@/components/Checklist"
import { closeAnimated, isVisible, onDockChange, togglePanel, toggleTray, trayCollapsed, type PanelId } from "@/lib/dock"
import { useApp } from "@/lib/store"
import { cn } from "@/lib/utils"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Kbd } from "@/components/ui/kbd"

/** The right tray's own controls, down its left edge (its tab strip; when the tray folds, this column is the rail):
 *  one icon per panel with its live badge (checklist progress, artifacts, running tasks). A click shows that
 *  panel, or folds the tray if it is the one showing. Other panels in the tray (open files, ...) follow. The fold
 *  button sits at the foot (TrayFold). These replace both the text tabs and the old top-bar buttons. */

const FIXED: { id: PanelId; label: string; icon: React.ReactNode }[] = [
  { id: "checklist", label: "Checklist", icon: <ListChecks /> },
  { id: "browser", label: "Browser", icon: <Globe /> },
  { id: "artifacts", label: "Artifacts", icon: <Layers /> },
  { id: "tasks", label: "Tasks", icon: <PanelRight /> },
]

function useDockTick() {
  const [, setTick] = useState(0)
  useEffect(() => { const off = onDockChange(() => setTick((x) => x + 1)); return () => { off() } }, [])
}

function IconButton({ on, label, onClick, children, badge, tone, onClose }: {
  on: boolean; label: string; onClick: () => void; children: React.ReactNode
  badge?: string | number | null; tone?: "info" | "warning"; onClose?: () => void
}) {
  return (
    <div className="group/tb relative w-full">
      {on && <span aria-hidden="true" className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-foreground/80" />}
      <Tooltip>
        <TooltipTrigger render={<button onClick={onClick} aria-label={label} aria-pressed={on}
          className={cn("mx-auto flex w-7 flex-col items-center gap-0.5 rounded-md py-1.5 transition-ui [&_svg]:size-4",
            on ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:bg-surface-2 hover:text-foreground")} />}>
          {children}
          {badge != null && badge !== "" && (
            <span data-tone={tone} className={cn("mono text-[10px] leading-none tabular-nums", tone ? "tone-text" : "text-muted-foreground")}>{badge}</span>
          )}
        </TooltipTrigger>
        <TooltipContent side="left">{label}</TooltipContent>
      </Tooltip>
      {onClose && (
        <button onClick={onClose} aria-label={`Close ${label}`} title={`Close ${label}`}
          className="absolute -right-0.5 -top-0.5 hidden size-3.5 place-items-center rounded-full bg-surface-3 text-muted-foreground hover:text-foreground group-hover/tb:grid">
          <X className="size-2.5" />
        </button>
      )}
    </div>
  )
}

/** Top of the tray's strip: the panel icons. Renders nothing for other groups. */
export function TrayPanels(props: IDockviewHeaderActionsProps) {
  useDockTick()
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  const tasks = useApp((s) => s.tasks)
  if (props.group.id !== "tray") return null
  const checklist = conv ? latestChecklist(conv.messages) : null
  const p = checklist ? progress(checklist) : null
  const artifactCount = conv ? artifactsIn(conv.messages).length + filesIn(conv.messages).length : 0
  const active = tasks.filter((t) => t.status === "running" || t.status === "needs approval").length
  const waiting = tasks.some((t) => t.status === "needs approval")
  const badges: Record<string, { badge?: string | number | null; tone?: "info" | "warning" }> = {
    checklist: { badge: p ? `${p.done}/${p.total}` : null },
    artifacts: { badge: artifactCount || null },
    tasks: { badge: active || null, tone: active ? (waiting ? "warning" : "info") : undefined },
  }
  const fixedIds = new Set<string>(FIXED.map((f) => f.id))
  const extra = props.group.panels.filter((x) => !fixedIds.has(x.id))
  return (
    <div className="flex w-full flex-col items-center gap-1 py-1.5">
      {FIXED.map((f) => (
        <IconButton key={f.id} on={isVisible(f.id)} label={f.label} onClick={() => togglePanel(f.id)} {...badges[f.id]}>{f.icon}</IconButton>
      ))}
      {extra.length > 0 && <span aria-hidden="true" className="my-0.5 h-px w-5 bg-line-2" />}
      {extra.map((x) => (
        <IconButton key={x.id} on={isVisible(x.id)} label={x.title || x.id}
          onClick={() => { if (isVisible(x.id)) toggleTray(); else { if (trayCollapsed()) toggleTray(); x.api.setActive() } }}
          onClose={() => closeAnimated(x)}>
          {x.id === "devtools" ? <SquareTerminal /> : <FileText />}
        </IconButton>
      ))}
    </div>
  )
}

/** Foot of the tray's strip: fold / unfold (the tray's bottom-left corner; the rail's foot when folded). */
export function TrayFold(props: IDockviewHeaderActionsProps) {
  useDockTick()
  if (props.group.id !== "tray") return null
  const folded = trayCollapsed()
  return (
    <div className="flex w-full items-center justify-center py-1.5">
      <Tooltip>
        <TooltipTrigger render={<button onClick={() => toggleTray()} aria-label={folded ? "Show side panels" : "Hide side panels"} aria-pressed={!folded}
          className="grid size-7 place-items-center rounded-md text-muted-foreground transition-ui hover:bg-surface-2 hover:text-foreground" />}>
          {folded ? <PanelRightOpen className="size-4" /> : <PanelRightClose className="size-4" />}
        </TooltipTrigger>
        <TooltipContent side="left">{folded ? "Show side panels" : "Hide side panels"} <Kbd>Ctrl+Alt+B</Kbd></TooltipContent>
      </Tooltip>
    </div>
  )
}
