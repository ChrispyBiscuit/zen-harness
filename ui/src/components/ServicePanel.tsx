import { useEffect, useState } from "react"
import { Copy, Plug, Plus, RefreshCw, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Field, More, Section, Toggle, inputCls } from "@/components/SettingsDialog"

/* Settings → Connect apps: let other programs use Zen Harness (REST API, MCP server, background tray service).
   Backend: harness_api.py, harness_mcp.py, headless.py; user guide: docs/connect-apps.md. */

export const HEADLESS_DEFAULTS = {
  autostart: false, api_enabled: true, mcp_enabled: true, port: 8765, lan: false, token: "",
  approval: "deny", approval_timeout: 120, allow_rules: [] as { tool: string; pattern: string }[],
  max_queue: 20, rate_limit: 120, max_run_minutes: 30,
}
export type Headless = typeof HEADLESS_DEFAULTS

type Status = {
  resident: boolean; paused: boolean; busy: boolean; busy_title: string; queued: number; port: number
  autostart_registered: boolean; base_url: string; stdio_command: { command: string; args: string[] }
}

function copy(text: string, what: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${what} copied`), () => toast.error("Could not copy"))
}

function Snippet({ label, text }: { label: string; text: string }) {
  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between text-caption text-muted-foreground">
        <span>{label}</span>
        <Button size="xs" variant="ghost" onClick={() => copy(text, label)}><Copy /> Copy</Button>
      </div>
      <pre className="mono overflow-x-auto rounded-lg border border-line-1 bg-surface-1 p-2 text-caption leading-relaxed">{text}</pre>
    </div>
  )
}

export function ServicePanel({ h, setH }: { h: Headless; setH: (p: Partial<Headless>) => void }) {
  const [st, setSt] = useState<Status | null>(null)
  const [show, setShow] = useState(false)
  useEffect(() => {
    let on = true
    const load = () => fetch("/api/headless/status").then((r) => r.json()).then((s) => on && setSt(s)).catch(() => {})
    load()
    const t = window.setInterval(load, 3000)
    return () => { on = false; window.clearInterval(t) }
  }, [])
  const rules = h.allow_rules || []
  const base = st?.base_url || `http://127.0.0.1:${h.port}`
  const tok = h.token || "<token>"
  const masked = h.token ? h.token.slice(0, 4) + "•".repeat(20) + h.token.slice(-4) : "(created on first start)"
  const stdio = st?.stdio_command
  const pause = async (paused: boolean) => {
    const r = await fetch("/api/headless/pause", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused }) })
    setSt({ ...(st as Status), ...(await r.json()) })
  }
  const regen = async () => {
    if (!confirm("Make a new API token? Programs using the old one stop working until you give them the new one.")) return
    const r = await fetch("/api/headless/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((x) => x.json())
    setH({ token: r.token })
    toast.success("New API token created")
  }
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null)
  const testConnection = async () => {
    setTest(null)
    try {
      const health = await fetch(`${base}/v1/health`).then((r) => r.json())
      const r = await fetch(`${base}/v1/runs?limit=1`, { headers: { Authorization: `Bearer ${h.token}` } })
      if (r.status === 401) return setTest({ ok: false, text: "Reachable, but the token was refused — save Settings first if you just changed it." })
      if (r.status === 403) return setTest({ ok: false, text: "Reachable, but the REST API is switched off." })
      setTest({ ok: r.ok, text: r.ok ? `Working — port ${health.port}${health.busy ? ", busy right now" : ""}.` : `Unexpected answer: ${r.status}` })
    } catch {
      setTest({ ok: false, text: "Not reachable on this port." })
    }
  }
  const num = (k: keyof Headless, label: string, min: number, max: number) => (
    <label className="flex items-center gap-1.5 text-caption text-muted-foreground">{label}
      <input type="number" min={min} max={max} value={h[k] as number} onChange={(e) => setH({ [k]: Number(e.target.value) } as any)} className={inputCls.replace("w-full", "w-20")} />
    </label>
  )

  return (
    <>
      <Section title="Background service">
        <div className="rounded-lg border border-line-1 bg-surface-1 px-3 py-2 text-ui">
          {!st ? <span className="text-muted-foreground">Checking…</span> : (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span data-tone={st.resident ? "success" : "neutral"} className={st.resident ? "text-success" : "text-muted-foreground"}>●</span>{" "}
                {st.resident ? "Running in the tray" : "Not running — this window's server answers the API until you close it"}
                <span className="text-muted-foreground">
                  {" · "}{st.busy ? `busy: ${st.busy_title || "a task"}` : "idle"}{st.queued ? ` · ${st.queued} queued` : ""}{st.paused ? " · API paused" : ""}
                </span>
              </span>
              <Button size="xs" variant="outline" onClick={() => pause(!st.paused)}>{st.paused ? "Resume API runs" : "Pause API runs"}</Button>
            </div>
          )}
        </div>
        <Toggle checked={!!h.autostart} onChange={(v) => setH({ autostart: v })} label="Run in the background at sign-in"
          hint={"Runs the Zen Headless Harness in the tray when you sign in to Windows, with no window: other programs can use it any time. "
            + "Adds one shortcut to your Startup folder (just your account, no admin; you can also turn it off in Task Manager → Startup apps)."
            + (st ? (st.autostart_registered ? " Currently on." : " Currently off.") : "")} />
        <div className="text-caption text-muted-foreground">Without it, other apps can reach Zen Harness while this window is open; MCP over stdio starts it on demand.</div>
      </Section>

      <Section title="Access">
        <Toggle checked={!!h.api_enabled} onChange={(v) => setH({ api_enabled: v })} label="REST API" hint={`${base}/v1 — submit tasks, stream events, get results.`} />
        <Toggle checked={!!h.mcp_enabled} onChange={(v) => setH({ mcp_enabled: v })} label="MCP server" hint={`${base}/mcp (HTTP) or the stdio command below — tools: run_task, get_run, cancel_run, list_runs, list_conversations, get_conversation, get_status.`} />
        <Field label="API token" hint="Programs send it as “Authorization: Bearer <token>”. Keep it private.">
          <div className="flex items-center gap-1.5">
            <input readOnly value={show ? h.token : masked} className={inputCls + " mono text-caption"} onFocus={() => setShow(true)} onBlur={() => setShow(false)} />
            <Button size="icon-sm" variant="ghost" aria-label="Copy token" onClick={() => copy(h.token, "Token")} disabled={!h.token}><Copy /></Button>
            <Button size="icon-sm" variant="ghost" aria-label="New token" onClick={regen}><RefreshCw /></Button>
          </div>
        </Field>
        <Field label="Port" hint={"The app and the API share it. Applies after a restart of the service/app."
          + (st && st.port !== h.port ? ` Running on ${st.port} now.` : "")}>
          <input type="number" min={1024} max={65535} value={h.port} onChange={(e) => setH({ port: Number(e.target.value) })} className={inputCls + " w-28"} />
        </Field>
        <Toggle checked={!!h.lan} onChange={(v) => setH({ lan: v })} label="Allow other devices on the network"
          hint="Off: only this PC can connect. On: the API and MCP (never the app itself) answer other devices that have the token; no encryption, so only on a network you trust. Applies after a restart; Windows Firewall will ask." />
      </Section>

      <Section title="What tasks from other apps may do">
        <Field label="When a run needs approval" hint="Reads, web research and read-only commands follow Permissions as usual. This decides everything that would show an approval card.">
          <select value={h.approval} onChange={(e) => setH({ approval: e.target.value })} className={inputCls}>
            <option value="deny">Deny it (the agent works around it)</option>
            <option value="ask">Ask me in the app, deny if nobody answers</option>
          </select>
          {h.approval === "ask" && <div className="mt-2">{num("approval_timeout", "deny after (s)", 10, 3600)}</div>}
        </Field>
        <Field label="Always allowed for API runs" hint="Only these rules apply to API runs (your Permissions → Always allowed list does not). * is a wildcard.">
          <div className="space-y-1.5">
            {rules.length === 0 && <div className="text-caption text-muted-foreground">None — nothing that needs approval runs.</div>}
            {rules.map((r, i) => (
              <div key={i} className="flex gap-1.5">
                <input value={r.tool} onChange={(e) => setH({ allow_rules: rules.map((x, j) => j === i ? { ...x, tool: e.target.value } : x) })} className={inputCls + " mono w-32 text-caption"} />
                <input value={r.pattern} onChange={(e) => setH({ allow_rules: rules.map((x, j) => j === i ? { ...x, pattern: e.target.value } : x) })} className={inputCls + " mono text-caption"} />
                <Button size="icon-sm" variant="ghost" onClick={() => setH({ allow_rules: rules.filter((_, j) => j !== i) })} aria-label="Remove"><Trash2 /></Button>
              </div>
            ))}
            <Button size="xs" variant="outline" onClick={() => setH({ allow_rules: [...rules, { tool: "write_file", pattern: "*" }] })}><Plus /> Add rule</Button>
          </div>
        </Field>
        <More>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {num("max_queue", "queue size", 1, 500)}{num("rate_limit", "requests/min", 1, 10000)}{num("max_run_minutes", "max run (min)", 1, 1440)}
          </div>
        </More>
      </Section>

      <Section title="Connect an app">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={testConnection}><Plug /> Test connection</Button>
          {test && <span className={test.ok ? "text-caption text-success" : "text-caption text-destructive"}>{test.text}</span>}
        </div>
        {stdio && <Snippet label="Claude Desktop — claude_desktop_config.json (Settings → Developer → Edit Config)"
          text={JSON.stringify({ mcpServers: { "zen-harness": { command: stdio.command, args: stdio.args } } }, null, 2)} />}
        <Snippet label="Claude Code" text={`claude mcp add zen-harness --transport http ${base}/mcp --header "Authorization: Bearer ${tok}"`} />
        <Snippet label="Cursor / VS Code — mcp.json" text={JSON.stringify({ servers: { "zen-harness": { type: "http", url: `${base}/mcp`, headers: { Authorization: `Bearer ${tok}` } } } }, null, 2)} />
        <Snippet label="Any MCP client over HTTP" text={JSON.stringify({ mcpServers: { "zen-harness": { type: "http", url: `${base}/mcp`, headers: { Authorization: `Bearer ${tok}` } } } }, null, 2)} />
        <Snippet label="REST API (curl)" text={`curl -H "Authorization: Bearer ${tok}" -H "Content-Type: application/json" \\\n  -d "{\\"input\\": \\"Summarise today's AI news\\", \\"wait\\": 300}" ${base}/v1/runs`} />
        <Snippet label="Python" text={`import json, urllib.request
req = urllib.request.Request("${base}/v1/runs", method="POST",
    data=json.dumps({"input": "What changed in Python 3.14?", "wait": 300}).encode(),
    headers={"Authorization": "Bearer ${tok}", "Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=330))["run"]["result"])`} />
        <div className="mt-2 text-caption text-muted-foreground">Full guide: Help → Connect other apps.</div>
      </Section>
    </>
  )
}
