import { useEffect, useRef, useState } from "react"
import { textFx } from "@/lib/textfx"

const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghkmnpqrstuvwxyz0123456789@%&?ΔΘΛΞΣΦΨΩ░▒▓"
const REDUCED = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches

/** A short label that decodes when it changes ("Thinking" → "Reasoning", a new model name): for ~220 ms the new
 *  text shows as glyphs that lock in left to right, with the chromatic split of the streaming reply (index.css
 *  .decoding). The first render, Text effects Off and reduced motion show the text as it is. */
export function Decode({ text, ms = 220 }: { text: string; ms?: number }) {
  const [shown, setShown] = useState(text)
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; setShown(text); return }
    if (REDUCED || textFx() === "off" || !text) { setShown(text); return }
    const t0 = performance.now()
    let raf = 0
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / ms)
      const n = Math.floor(text.length * k)
      setShown(text.slice(0, n) + [...text.slice(n)].map((c) => (/\s/.test(c) ? c : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join(""))
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [text, ms])
  return <span className={shown !== text ? "decoding" : undefined} aria-label={text}><span aria-hidden="true">{shown}</span></span>
}
