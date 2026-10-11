import { useEffect, useMemo, useState } from "react"
import { Loader2, Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"

type Voice = { id: string; name: string; language: string; gender: string }
const inputCls = "w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-ui outline-none focus:border-ring"

/** Settings → Voice: narrator (optionally blended with a second one), speed, volume, auto-read. */
export function VoicePanel({ ui, setUi }: { ui: Record<string, any>; setUi: (p: Record<string, any>) => void }) {
  const [voices, setVoices] = useState<Voice[] | null>(null)
  const [avail, setAvail] = useState(true)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    fetch("/api/tts/voices").then((r) => r.json()).then((d) => { setAvail(d.available); setVoices(d.voices) }).catch(() => setAvail(false))
  }, [])
  const groups = useMemo(() => {
    const g: Record<string, Voice[]> = {}
    for (const v of voices || []) (g[v.language] ||= []).push(v)
    return g
  }, [voices])
  const voice = ui.tts_voice || "af_heart"
  const speed = Number(ui.tts_speed || 1)
  const blend = Number(ui.tts_blend || 0)
  const preview = async () => {
    setBusy(true)
    try {
      const v = blend && ui.tts_voice2 ? `${voice}:${100 - blend},${ui.tts_voice2}:${blend}` : voice
      const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "Hi! This is how I'll sound when I read replies aloud. Adjust my voice and speed until it feels right.", voice: v, speed }) })
      if (!r.ok) throw new Error((await r.json()).error)
      const a = new Audio(URL.createObjectURL(await r.blob()))
      a.volume = ui.tts_volume == null ? 1 : Number(ui.tts_volume)
      await a.play()
    } catch (e) { console.error(e) } finally { setBusy(false) }
  }
  const select = (value: string, onChange: (v: string) => void, allowNone = false) => (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
      {allowNone && <option value="">— none —</option>}
      {Object.entries(groups).map(([lang, vs]) => (
        <optgroup key={lang} label={lang}>
          {vs.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.gender})</option>)}
        </optgroup>
      ))}
    </select>
  )
  if (!avail) return <p className="py-4 text-ui text-warning">The voice model is not installed (expected in ~/models/kokoro).</p>
  if (!voices) return <p className="py-4 text-ui text-muted-foreground"><Loader2 className="mr-1 inline size-3.5 animate-spin" />Loading voices…</p>
  const row = "grid grid-cols-[150px_1fr] items-center gap-3 py-2"
  return (
    <div className="space-y-1 py-2 text-ui">
      <p className="pb-2 text-caption text-muted-foreground">Replies are read by Kokoro, a small voice model running on your CPU (not the GPU). The speaker button on any reply reads it aloud.</p>
      <div className={row}><span>Narrator</span>{select(voice, (v) => setUi({ tts_voice: v }))}</div>
      <div className={row}><span>Blend with</span>{select(ui.tts_voice2 || "", (v) => setUi({ tts_voice2: v, tts_blend: v ? blend || 30 : 0 }), true)}</div>
      {ui.tts_voice2 && (
        <div className={row}><span>Mix</span>
          <div className="flex items-center gap-2"><input type="range" min={0} max={100} step={5} value={blend} onChange={(e) => setUi({ tts_blend: Number(e.target.value) })} className="flex-1" />
            <span className="mono w-24 text-right text-caption text-muted-foreground">{100 - blend}% / {blend}%</span></div></div>
      )}
      <div className={row}><span>Speed</span>
        <div className="flex items-center gap-2"><input type="range" min={0.5} max={2} step={0.05} value={speed} onChange={(e) => setUi({ tts_speed: Number(e.target.value) })} className="flex-1" />
          <span className="mono w-24 text-right text-caption text-muted-foreground">{speed.toFixed(2)}×</span></div></div>
      <div className={row}><span>Volume</span>
        <div className="flex items-center gap-2"><input type="range" min={0} max={1} step={0.05} value={ui.tts_volume == null ? 1 : ui.tts_volume} onChange={(e) => setUi({ tts_volume: Number(e.target.value) })} className="flex-1" />
          <span className="mono w-24 text-right text-caption text-muted-foreground">{Math.round((ui.tts_volume == null ? 1 : ui.tts_volume) * 100)}%</span></div></div>
      <div className={row}><span>Read replies aloud</span>
        <label className="flex items-center gap-2"><Switch checked={!!ui.tts_auto} onCheckedChange={(v: boolean) => setUi({ tts_auto: v })} />
          <span className="text-caption text-muted-foreground">automatically, sentence by sentence as each reply arrives</span></label></div>
      <div className={row}><span>Voice input</span>
        <select value={ui.stt_model || "base.en"} onChange={(e) => setUi({ stt_model: e.target.value })} className={inputCls}>
          <option value="base.en">Whisper base (English) — fastest, ~8× real time</option>
          <option value="small.en">Whisper small (English) — more accurate, ~3× real time</option>
          <option value="small">Whisper small (multilingual)</option>
        </select></div>
      <div className="pt-2"><Button size="sm" variant="outline" onClick={preview} disabled={busy}>{busy ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />} Preview</Button></div>
    </div>
  )
}
