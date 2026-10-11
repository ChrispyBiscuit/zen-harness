/** Comfortable (default) or compact spacing and type in the transcript and sidebar (index.css: data-density).
 *  Stored in the UI settings (settings.ui.density) and mirrored in localStorage so it applies before they load. */
import { useApp } from "./store"

export type Density = "comfortable" | "compact"
const KEY = "zen.density"

export function applyDensity(d: Density) {
  document.documentElement.dataset.density = d
  try { localStorage.setItem(KEY, d) } catch { /* private window */ }
}

export function storedDensity(): Density {
  try { return localStorage.getItem(KEY) === "compact" ? "compact" : "comfortable" } catch { return "comfortable" }
}

/** Apply at once, then persist to the UI settings (a failed save keeps the local choice). */
export async function setDensity(d: Density) {
  applyDensity(d)
  try { await useApp.getState().saveSettings({ ui: { density: d } } as any) } catch { /* offline or replay */ }
}
