import { useEffect, useState } from "react"
import { DockviewDefaultTab, DockviewReact, type DockviewTheme, type IDockviewHeaderActionsProps,
  type IDockviewPanelHeaderProps, type IDockviewPanelProps } from "dockview-react"
import "dockview-react/dist/styles/dockview.css"
import "@/dock-theme.css"
import { Maximize2, Minimize2, PictureInPicture2 } from "lucide-react"
import { Transcript } from "./Transcript"
import { Composer } from "./Composer"
import { WorkerTiles } from "./WorkerTiles"
import { Empty } from "./EmptyState"
import { BrowserView } from "./BrowserView"
import { DevToolsView } from "./DevToolsView"
import { ChecklistPanel, FileView, TasksView } from "./SidePanel"
import { ArtifactsPanel } from "./ArtifactsPanel"
import { closeAnimated, dockApi, initDock, toggleGroupMaximized } from "@/lib/dock"
import { TrayFold, TrayPanels } from "./TrayBar"
import { useApp } from "@/lib/store"

const theme: DockviewTheme = {
  name: "app", className: "dockview-theme-app", colorScheme: "dark", gap: 6,
  dndOverlayMounting: "relative", dndPanelOverlay: "group", dndTabIndicator: "line", tabAnimation: "smooth",
}

function ChatPanel() {
  const current = useApp((s) => s.current)
  const conv = useApp((s) => (s.current ? s.convs[s.current] : null))
  return (
    <div className="zen-panel-in chat-wash flex h-full min-w-0 flex-col bg-background">
      {current && conv ? <Transcript conv={conv} /> : current ? <div className="flex-1" /> : <Empty />}
      {conv && <WorkerTiles conv={conv} />}
      <Composer />
    </div>
  )
}

// a tab's content fades up when it is shown (dockview mounts it only while visible)
const Wrap = ({ children }: { children: React.ReactNode }) => <div className="zen-panel-in h-full overflow-hidden bg-background">{children}</div>

/** The stock tab, but closing plays the exit animation first (lib/dock.ts closeAnimated). */
function Tab(props: IDockviewPanelHeaderProps) {
  if (props.api.id === "chat") return <DockviewDefaultTab {...props} hideClose />
  return <DockviewDefaultTab {...props} closeActionOverride={() => {
    const p = props.containerApi.getPanel(props.api.id)
    if (p) closeAnimated(p)
  }} />
}

const components: Record<string, React.FunctionComponent<IDockviewPanelProps>> = {
  chat: () => <ChatPanel />,
  checklist: () => <Wrap><ChecklistPanel /></Wrap>,
  browser: () => <Wrap><BrowserView /></Wrap>,
  devtools: () => <Wrap><DevToolsView /></Wrap>,
  tasks: () => <Wrap><TasksView /></Wrap>,
  artifacts: () => <Wrap><ArtifactsPanel /></Wrap>,
  file: (p: IDockviewPanelProps<{ path: string }>) => <Wrap><FileView key={p.params.path} path={p.params.path} /></Wrap>,
}

/** Float / maximize buttons at the right end of every group's tab bar. */
function GroupActions(props: IDockviewHeaderActionsProps) {
  const [max, setMax] = useState(props.api.isMaximized())
  const floating = props.api.location.type === "floating"
  useEffect(() => {
    const d = props.containerApi.onDidMaximizedGroupChange(() => setMax(props.api.isMaximized()))
    return () => d.dispose()
  }, [props.api, props.containerApi])
  if (props.api.location.type === "edge") return <TrayFold {...props} />     // the tray's foot: its fold button
  const btn = "grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
  return (
    <div className="flex h-full items-center gap-0.5 px-1.5">
      {!floating && (
        <button className={btn} title="Float this group" onClick={() => props.activePanel &&
          props.containerApi.addFloatingGroup(props.group, { width: 680, height: 500, x: 140, y: 90 } as any)}>
          <PictureInPicture2 className="size-3.5" />
        </button>
      )}
      {!floating && (
        <button className={btn} title={max ? "Restore" : "Maximize"} onClick={() => (max ? props.api.exitMaximized() : props.api.maximize())}>
          {max ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </button>
      )}
    </div>
  )
}

/** Double-click on a tab or the empty part of a tab bar: maximise / restore that group (a tray folds away). */
function onDoubleClick(e: React.MouseEvent) {
  const t = e.target as HTMLElement
  if (!t.closest(".dv-tabs-and-actions-container") || t.closest("button, input, .dv-default-tab-action")) return
  const groupEl = t.closest(".dv-groupview")
  const g = dockApi()?.groups.find((x) => x.element === groupEl || x.element.contains(groupEl))
  if (g) toggleGroupMaximized(g)
}

export function Workspace() {
  return (
    <div className="min-h-0 flex-1 p-1.5" onDoubleClick={onDoubleClick}>
      <DockviewReact className="h-full" theme={theme} components={components} floatingGroupBounds="boundedWithinViewport"
        rightHeaderActionsComponent={GroupActions} defaultTabComponent={Tab} prefixHeaderActionsComponent={TrayPanels} onReady={(e) => initDock(e.api)} />
    </div>
  )
}
