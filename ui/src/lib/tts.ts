// Read-aloud player: splits text into sentences, synthesizes them on the backend (Kokoro, CPU) one or two ahead,
// and plays them in order. feed() can be called repeatedly while a reply streams (progressive reading).
import { useApp } from "./store"

type Item = { text: string; audio?: Promise<string | null> }

function speechText(md: string) {
  return md
    .replace(/```[\s\S]*?(```|$)/g, " (code omitted). ")
    .replace(/^\s*\|.*\|\s*$/gm, " ")
    .replace(/\n{2,}/g, ". ")
}

function sentences(text: string): string[] {
  return text.split(/(?<=[.!?:;])\s+(?=[A-Z0-9"'(\[])|\.\s+(?=\S)|\n+/).map((s) => s.trim()).filter((s) => s.replace(/[^\w]/g, "").length > 1)
}

class Speaker {
  key: string | null = null
  private queue: Item[] = []
  private consumed = 0          // characters of the (speech-cleaned) text already queued
  private audio: HTMLAudioElement | null = null
  private playing = false
  private gen = 0

  private settings() {
    const ui: any = useApp.getState().settings?.ui || {}
    const blend = ui.tts_blend && ui.tts_voice2 ? `${ui.tts_voice || "af_heart"}:${100 - ui.tts_blend},${ui.tts_voice2}:${ui.tts_blend}` : (ui.tts_voice || "af_heart")
    return { voice: blend, speed: Number(ui.tts_speed || 1), volume: ui.tts_volume == null ? 1 : Number(ui.tts_volume) }
  }

  private synth(text: string, gen: number): Promise<string | null> {
    const { voice, speed } = this.settings()
    return fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, voice, speed }) })
      .then((r) => (r.ok && gen === this.gen ? r.blob() : null))
      .then((b) => (b ? URL.createObjectURL(b) : null))
      .catch(() => null)
  }

  /** Queue the not-yet-read part of `text`. done=false: only complete sentences (the reply is still streaming). */
  feed(key: string, text: string, done = true) {
    if (this.key !== key) this.reset(key)
    const clean = speechText(text)
    let upto = clean.length
    if (!done) {
      const m = [...clean.matchAll(/[.!?:;](\s|$)|\n/g)]
      upto = m.length ? (m[m.length - 1].index ?? 0) + 1 : 0
    }
    if (upto <= this.consumed) return
    const chunk = clean.slice(this.consumed, upto)
    this.consumed = upto
    for (const s of sentences(chunk)) this.queue.push({ text: s })
    this.prefetch()
    if (!this.playing) this.next()
  }

  private prefetch() {
    const gen = this.gen
    for (const it of this.queue.slice(0, 2)) if (!it.audio) it.audio = this.synth(it.text, gen)
  }

  private async next(): Promise<void> {
    const gen = this.gen
    const it = this.queue.shift()
    if (!it) { this.playing = false; this.publish(); return }
    this.playing = true
    this.publish()
    if (!it.audio) it.audio = this.synth(it.text, gen)
    this.prefetch()
    const url = await it.audio
    if (gen !== this.gen) return
    if (!url) return this.next()
    const a = new Audio(url)
    a.volume = Math.max(0, Math.min(1, this.settings().volume))
    this.audio = a
    const done = () => { URL.revokeObjectURL(url); if (gen === this.gen) this.next() }
    a.onended = done
    a.onerror = done
    try { await a.play() } catch { this.next() }
  }

  private reset(key: string | null) {
    this.gen++
    this.audio?.pause()
    this.audio = null
    this.queue = []
    this.consumed = 0
    this.playing = false
    this.key = key
  }

  stop() {
    this.reset(null)
    this.publish()
  }

  private publish() {
    useApp.setState({ speaking: this.playing || this.queue.length ? this.key : null } as any)
  }
}

export const speaker = new Speaker()
