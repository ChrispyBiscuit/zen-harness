/** How a reply looks while it streams in (index.css: data-text-fx). The decode tail, chromatic-aberration fringes
 *  and glitch bursts are the app's one signature effect; this sets how loud it is.
 *    full    wider fringes, frequent bursts      signal  the default
 *    calm    decode tail and static fringes, no bursts      off  plain caret only
 *  Stored in the UI settings (settings.ui.text_fx) and mirrored in localStorage so it applies before they load. */
import { useApp } from "./store"

export type TextFx = "full" | "signal" | "calm" | "off"
export const TEXT_FX: TextFx[] = ["full", "signal", "calm", "off"]
const KEY = "zen.textfx"

export function applyTextFx(v: TextFx) {
  document.documentElement.dataset.textFx = v
  try { localStorage.setItem(KEY, v) } catch { /* private window */ }
}

export function storedTextFx(): TextFx {
  try { const v = localStorage.getItem(KEY) as TextFx; return TEXT_FX.includes(v) ? v : "signal" } catch { return "signal" }
}

/** The level in force right now (the attribute is set at start-up, before any reply streams). */
export function textFx(): TextFx {
  const v = document.documentElement.dataset.textFx as TextFx
  return TEXT_FX.includes(v) ? v : "signal"
}

/** Apply at once, then persist to the UI settings (a failed save keeps the local choice). */
export async function setTextFx(v: TextFx) {
  applyTextFx(v)
  try { await useApp.getState().saveSettings({ ui: { text_fx: v } } as any) } catch { /* offline or replay */ }
}
