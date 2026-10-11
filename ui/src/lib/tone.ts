/** One state-tone system for pills, card edges, icons, meters and progress (index.css: data-tone + tone-* utilities).
 *  info = working, success = done, warning = needs attention, destructive = failed, muted = inactive. */
export type Tone = "info" | "success" | "warning" | "destructive" | "muted"

/** Tone for a fill level: fine under `warn`%, attention under `bad`%, failed above. */
export function levelTone(pct: number | undefined, warn = 70, bad = 90): Tone {
  if (pct == null) return "muted"
  return pct > bad ? "destructive" : pct > warn ? "warning" : "success"
}
