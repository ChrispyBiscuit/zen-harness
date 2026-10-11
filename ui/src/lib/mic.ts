// Microphone capture -> 16 kHz mono WAV -> /api/stt. Optional voice-activity detection: start when you speak,
// stop after a short silence (hands-free conversation).

type Opts = { vad?: boolean; silenceMs?: number; maxMs?: number; onLevel?: (v: number) => void; onSpeech?: () => void }

function encodeWav(samples: Float32Array, rate: number) {
  const buf = new ArrayBuffer(44 + samples.length * 2)
  const v = new DataView(buf)
  const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)) }
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ")
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true)
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data")
  v.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([buf], { type: "audio/wav" })
}

function downsample(data: Float32Array, from: number, to = 16000) {
  if (from === to) return data
  const ratio = from / to
  const out = new Float32Array(Math.floor(data.length / ratio))
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(data.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j++) sum += data[j]
    out[i] = sum / Math.max(1, end - start)
  }
  return out
}

export class Recorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: ScriptProcessorNode | null = null
  private chunks: Float32Array[] = []
  private resolve: ((b: Blob | null) => void) | null = null
  private heard = false
  private lastVoice = 0
  private started = 0
  active = false

  /** Record until stop() (or, with vad, until the speaker pauses). Resolves with a WAV blob, or null if nothing was said. */
  async record(opts: Opts = {}): Promise<Blob | null> {
    const silenceMs = opts.silenceMs ?? 900, maxMs = opts.maxMs ?? 30000
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    this.ctx = new AudioContext()
    const src = this.ctx.createMediaStreamSource(this.stream)
    this.node = this.ctx.createScriptProcessor(4096, 1, 1)
    this.chunks = []
    this.heard = !opts.vad
    this.started = this.lastVoice = performance.now()
    this.active = true
    const done = new Promise<Blob | null>((r) => (this.resolve = r))
    this.node.onaudioprocess = (e) => {
      const d = new Float32Array(e.inputBuffer.getChannelData(0))
      let rms = 0
      for (let i = 0; i < d.length; i++) rms += d[i] * d[i]
      rms = Math.sqrt(rms / d.length)
      opts.onLevel?.(Math.min(1, rms * 12))
      const now = performance.now()
      if (rms > 0.02) {
        if (!this.heard) opts.onSpeech?.()
        this.heard = true
        this.lastVoice = now
      }
      if (this.heard) this.chunks.push(d)
      else if (this.chunks.length < 3) this.chunks.push(d)     // keep a little audio from just before speech
      else this.chunks = [...this.chunks.slice(1), d]
      if (opts.vad && this.heard && now - this.lastVoice > silenceMs) this.stop()
      if (now - this.started > maxMs) this.stop()
    }
    src.connect(this.node)
    this.node.connect(this.ctx.destination)
    return done
  }

  stop(cancel = false) {
    if (!this.active) return
    this.active = false
    const rate = this.ctx?.sampleRate || 48000
    this.node?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    this.ctx?.close()
    const total = this.chunks.reduce((n, c) => n + c.length, 0)
    const all = new Float32Array(total)
    let o = 0
    for (const c of this.chunks) { all.set(c, o); o += c.length }
    const r = this.resolve
    this.resolve = null
    r?.(cancel || !this.heard || total < rate * 0.3 ? null : encodeWav(downsample(all, rate), 16000))
  }
}

export async function transcribe(wav: Blob): Promise<string> {
  const r = await fetch("/api/stt", { method: "POST", body: wav })
  const d = await r.json()
  if (d.error) throw new Error(d.error)
  return d.text || ""
}
