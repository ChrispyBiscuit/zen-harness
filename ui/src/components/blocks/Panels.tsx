import { memo, useState } from "react"
import { ArrowUpRight } from "lucide-react"
import { Lightbox } from "../ImageCarousel"
import { Frame } from "./Frame"
import { pairs, str } from "./parse"

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, "") } catch { return "" } }
const safeUrl = (u: string) => (/^https?:\/\//i.test(u) ? u : "")

/** ```facts: one person, place or thing at a glance; an optional picture beside the rows. */
export const Facts = memo(function Facts({ d }: { d: any }) {
  const items = pairs(d.items ?? d.facts ?? d.rows)
  const img = safeUrl(str(d.image))
  const [broken, setBroken] = useState(false)
  const [zoom, setZoom] = useState(false)
  if (!items.length && !d.title) return null
  return (
    <Frame title={str(d.title) || undefined} subtitle={str(d.subtitle) || undefined} className="max-w-xl">
      <div className="flex gap-4">
        <dl className="grid min-w-0 flex-1 grid-cols-[minmax(6rem,auto)_1fr] gap-x-4 text-ui">
          {items.map(([k, v], i) => (
            <div key={i} className="contents">
              <dt className={"py-1.5 text-faint " + (i ? "border-t border-line-1" : "")}>{k}</dt>
              <dd className={"py-1.5 text-foreground " + (i ? "border-t border-line-1" : "")}>{v}</dd>
            </div>
          ))}
        </dl>
        {img && !broken && (
          <button onClick={() => setZoom(true)} className="hidden shrink-0 self-start overflow-hidden rounded-[var(--radius-card)] ring-1 ring-line-1 sm:block">
            <img src={img} alt={str(d.title)} referrerPolicy="no-referrer" loading="lazy" onError={() => setBroken(true)}
              className="h-32 w-32 object-cover" />
          </button>
        )}
      </div>
      {zoom && <Lightbox list={[{ src: img, page: img, title: str(d.title) }]} start={0} onClose={() => setZoom(false)} />}
    </Frame>
  )
})

/** ```timeline: dated events on a vertical line, oldest first as written. */
export const Timeline = memo(function Timeline({ d }: { d: any }) {
  const rows: any[] = Array.isArray(d) ? d : Array.isArray(d?.events) ? d.events : Array.isArray(d?.items) ? d.items : []
  const title = Array.isArray(d) ? "" : str(d?.title)
  if (!rows.length) return null
  return (
    <Frame title={title || undefined} className="max-w-2xl">
      <ol className="relative ml-1.5 border-l border-line-2">
        {rows.map((r, i) => (
          <li key={i} className="relative pb-3 pl-4 last:pb-0">
            <span className="absolute -left-[4.5px] top-[7px] size-2 rounded-full bg-[var(--series-1)] ring-2 ring-surface-1" />
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-meta font-medium tabular-nums text-faint">{str(r.date ?? r.when ?? r.year)}</span>
              <span className="text-ui font-semibold text-foreground">{str(r.title ?? r.event ?? r.name)}</span>
            </div>
            {(r.text ?? r.description ?? r.detail) && <div className="mt-0.5 text-ui text-muted-foreground">{str(r.text ?? r.description ?? r.detail)}</div>}
          </li>
        ))}
      </ol>
    </Frame>
  )
})

/** ```cards: 2-5 options side by side, each with the same attribute rows. */
export const Cards = memo(function Cards({ d }: { d: any }) {
  const rows: any[] = Array.isArray(d) ? d : Array.isArray(d?.cards) ? d.cards : Array.isArray(d?.items) ? d.items : []
  if (!rows.length) return null
  return (
    <div className="answer-block not-prose my-3 grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(190px,1fr))]">
      {rows.slice(0, 8).map((c, i) => {
        const url = safeUrl(str(c.url ?? c.link))
        const items = pairs(c.items ?? c.specs ?? c.attributes)
        return (
          <div key={i} className="flex min-w-0 flex-col rounded-[var(--radius-panel)] bg-surface-1 p-3 ring-1 ring-line-1 shadow-[var(--hi)]">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-ui font-semibold leading-snug text-foreground">{str(c.title ?? c.name)}</div>
                {(c.subtitle ?? c.price) && <div className="mt-0.5 text-ui tabular-nums text-muted-foreground">{str(c.subtitle ?? c.price)}</div>}
              </div>
              {c.badge && <span className="shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[10.5px] font-medium text-foreground ring-1 ring-line-2">{str(c.badge)}</span>}
            </div>
            {items.length > 0 && (
              <dl className="mt-2 space-y-1 text-meta">
                {items.map(([k, v], j) => (
                  <div key={j} className="flex gap-2 border-t border-line-1 pt-1">
                    <dt className="text-faint">{k}</dt><dd className="ml-auto text-right text-foreground">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {(c.text ?? c.note) && <p className="mt-2 text-meta text-muted-foreground">{str(c.text ?? c.note)}</p>}
            {url && (
              <a href={url} target="_blank" rel="noreferrer"
                className="mt-auto flex items-center gap-1 pt-2 text-meta text-faint no-underline hover:text-foreground">
                {host(url)} <ArrowUpRight className="size-3" />
              </a>
            )}
          </div>
        )
      })}
    </div>
  )
})
