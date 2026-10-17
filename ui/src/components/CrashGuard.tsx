import { Component, type ErrorInfo, type ReactNode } from "react"

/* The whole UI is inside this. A rendering error used to unmount everything and leave a blank window (the agent run
   and the server kept going, but nothing showed it). Now the error is shown with Reload, and every UI error goes to
   <data>/ui_errors.log through /api/ui_error, so the cause is recorded. */

const version = () => ""                 // (the server knows its version; the log line has the time)

export function reportUiError(kind: string, err: unknown, componentStack?: string) {
  const e = err as { message?: string; stack?: string } | undefined
  const message = e?.message || String(err)
  try {
    void fetch("/api/ui_error", { method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
      body: JSON.stringify({ kind, message, stack: e?.stack || "", component_stack: componentStack || "",
        url: location.pathname + location.hash, version: version() }) }).catch(() => {})
  } catch { /* never let reporting throw */ }
}

/** Uncaught errors and rejected promises outside React's rendering (event handlers, timers, fetches). */
export function installErrorReporting() {
  window.addEventListener("error", (ev) => {
    if (ev.error || ev.message) reportUiError("uncaught", ev.error || ev.message)
  })
  window.addEventListener("unhandledrejection", (ev) => reportUiError("unhandled rejection", ev.reason))
}

type State = { error: Error | null }

export class CrashGuard extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportUiError("render", error, info.componentStack || "")
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24, background: "var(--background, #111)",
        color: "var(--foreground, #e8e9ec)", fontFamily: "system-ui, sans-serif" }}>
        <div style={{ maxWidth: 560, width: "100%" }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>The window hit an error</div>
          <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 12 }}>
            Your chats and any run in progress are safe: they live in the app's server, which is still running.
            Reloading the window brings everything back. The error is saved in ui_errors.log in the data folder.
          </div>
          <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 220, overflow: "auto",
            background: "rgba(127,127,127,0.12)", borderRadius: 8, padding: 10, margin: "0 0 14px" }}>
            {String(error.message || error)}
          </pre>
          <button onClick={() => location.reload()} autoFocus style={{ fontSize: 13, padding: "6px 14px", borderRadius: 8,
            border: "1px solid rgba(127,127,127,0.4)", background: "rgba(127,127,127,0.18)", color: "inherit", cursor: "pointer" }}>
            Reload the window
          </button>
        </div>
      </div>
    )
  }
}
