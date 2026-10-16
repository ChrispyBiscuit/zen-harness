import { lazy, memo, Suspense, useState } from "react"
import { rawUrl } from "@/lib/api"
import { CodeBlock } from "../CodeBlock"
import { Lightbox } from "../ImageCarousel"
import { Chart, normalizeChart } from "./Chart"
import { Pending, useLive } from "./Frame"
import { Cards, Facts, Timeline } from "./Panels"
import { parseBlock } from "./parse"

/* The rich blocks an answer can hold (backend/answer_shapes.py says when the model uses each one): a fenced block
   with one of these languages and a small JSON body. Unfinished while the reply streams → a placeholder; still
   unreadable once it's done → the source, with a note. Memoised on the source text, so the rest of a streaming
   reply doesn't redraw a finished block. */

export const BLOCK_LANGS = new Set(["chart", "map", "facts", "timeline", "cards", "mermaid"])
const NAMES: Record<string, string> = { chart: "chart", map: "map", facts: "fact panel", timeline: "timeline", cards: "cards", mermaid: "diagram" }

const MapBlock = lazy(() => import("./MapBlock"))
const MermaidBlock = lazy(() => import("./MermaidBlock"))

function Broken({ lang, code, why }: { lang: string; code: string; why: string }) {
  return (
    <div>
      <CodeBlock code={code} lang="json" />
      <div className="-mt-1 mb-2 text-meta text-faint">Couldn't draw this {NAMES[lang] || lang}: {why}</div>
    </div>
  )
}

export const AnswerBlock = memo(function AnswerBlock({ lang, code }: { lang: string; code: string }) {
  const live = useLive()
  const clean = code.replace(/[\uE000\uE001]/g, "")
  if (lang === "mermaid") {
    if (live) return <Pending kind="diagram" />
    return <Suspense fallback={<Pending kind="diagram" />}><MermaidBlock code={clean} /></Suspense>
  }
  const p = parseBlock(clean)
  if (!p.ok) return live ? <Pending kind={NAMES[lang]} height={lang === "map" ? 300 : 160} /> : <Broken lang={lang} code={clean} why={p.error} />
  const d = p.data
  switch (lang) {
    case "chart": {
      const spec = normalizeChart(d)
      return spec ? <Chart spec={spec} /> : <Broken lang={lang} code={clean} why="no numbers to plot" />
    }
    case "map": return <Suspense fallback={<Pending kind="map" height={300} />}><MapBlock d={d} /></Suspense>
    case "facts": return <Facts d={d} />
    case "timeline": return <Timeline d={d} />
    case "cards": return <Cards d={d} />
  }
  return <CodeBlock code={clean} lang={lang} />
}, (a, b) => a.lang === b.lang && a.code === b.code)

/** ![caption](url or file path) in an answer: the picture, captioned, click to view large. A local path (a figure
 *  the agent made) is served by /api/file. One that fails to load leaves only its caption as a link. */
export const InlineImage = memo(function InlineImage({ src, alt }: { src?: string; alt?: string }) {
  const [broken, setBroken] = useState(false)
  const [zoom, setZoom] = useState(false)
  if (!src) return null
  const local = /^[A-Za-z]:[\\/]/.test(src) || /^file:/i.test(src)
  const url = local ? rawUrl(src.replace(/^file:\/*/i, "")) : src
  if (broken) return alt ? <a href={local ? url : src} target="_blank" rel="noreferrer" className="link-ink">{alt}</a> : null
  return (
    <span className="answer-figure not-prose my-3 block">
      <button onClick={() => setZoom(true)} className="block overflow-hidden rounded-[var(--radius-panel)] bg-surface-2 ring-1 ring-line-1 transition-shadow hover:ring-line-3">
        <img src={url} alt={alt || ""} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} draggable={false}
          className="block max-h-[360px] max-w-full object-contain" />
      </button>
      {alt && <span className="mt-1.5 block text-meta text-faint">{alt}</span>}
      {zoom && <Lightbox list={[{ src: url, page: local ? url : src, title: alt }]} start={0} onClose={() => setZoom(false)} />}
    </span>
  )
})
