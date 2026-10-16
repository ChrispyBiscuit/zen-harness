import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { ArrowUpRight, ChevronLeft, ChevronRight, Images, X } from "lucide-react"
import { cn } from "@/lib/utils"

export type AnswerImage = {
  src: string; page: string; title?: string; cited?: boolean
  name?: string; publisher?: string; listing?: boolean      // what the page calls itself; listing = an article/list
}

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, "") } catch { return u } }
const favicon = (u: string) => { try { return new URL("/favicon.ico", u).href } catch { return "" } }
const label = (im: AnswerImage) => im.name || im.title || host(im.page)
const hideBroken = (e: React.SyntheticEvent<HTMLImageElement>) => { (e.target as HTMLImageElement).style.display = "none" }

/** One card: the photo with the place's (or article's) name over it, the site below the name. */
function Card({ im, onOpen, onBroken }: { im: AnswerImage; onOpen: () => void; onBroken: () => void }) {
  const [loaded, setLoaded] = useState(false)
  return (
    <button onClick={onOpen} title={`${label(im)} — ${host(im.page)}`}
      className="group/card relative h-56 w-[13.5rem] shrink-0 snap-start overflow-hidden rounded-[var(--radius-panel)] bg-surface-2 text-left
        outline-none ring-1 ring-line-1 transition-[box-shadow,transform] duration-300 ease-[var(--ease-out)]
        hover:-translate-y-0.5 hover:shadow-[0_12px_32px_-12px_rgba(0,0,0,0.6)] hover:ring-line-3 focus-visible:ring-2 focus-visible:ring-info/60">
      {!loaded && <div className="absolute inset-0 animate-pulse bg-gradient-to-br from-surface-2 via-surface-3 to-surface-2" />}
      <img src={im.src} alt={label(im)} referrerPolicy="no-referrer" loading="lazy" draggable={false}
        ref={(el) => { if (el?.complete && el.naturalWidth && !loaded) setLoaded(true) }}   // cached: no load event
        onLoad={() => setLoaded(true)} onError={onBroken}
        className={cn("absolute inset-0 size-full object-cover transition-[opacity,transform] duration-700 ease-[var(--ease-out)]",
          "group-hover/card:scale-[1.05]", loaded ? "opacity-100" : "opacity-0")} />
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/35 to-black/0" />
      <div className="absolute left-2.5 top-2.5 flex gap-1">
        {im.cited && <span className="rounded-full bg-white/90 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-black">Cited</span>}
        {im.listing && <span className="rounded-full bg-black/50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-white/85 ring-1 ring-white/15 backdrop-blur-md">Article</span>}
      </div>
      <div className="absolute inset-x-0 bottom-0 p-3">
        <div className={cn("line-clamp-2 font-semibold leading-snug text-white drop-shadow-sm",
          im.listing ? "text-[13px]" : "text-[15px]")}>{label(im)}</div>
        <div className="mt-1.5 flex min-w-0 items-center gap-1.5 text-[11px] text-white/70">
          <img src={favicon(im.page)} alt="" referrerPolicy="no-referrer" className="size-3.5 shrink-0 rounded-sm" onError={hideBroken} />
          <span className="truncate">{im.publisher || host(im.page)}</span>
          <ArrowUpRight className="ml-auto size-3.5 shrink-0 opacity-0 transition-opacity group-hover/card:opacity-100" />
        </div>
      </div>
    </button>
  )
}

/** Full-screen view: the image large, its page one click away; arrows, thumbnails and keys move between images. */
function Lightbox({ list, start, onClose }: { list: AnswerImage[]; start: number; onClose: () => void }) {
  const [i, setI] = useState(start)
  const go = useCallback((d: number) => setI((x) => (x + d + list.length) % list.length), [list.length])
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      else if (e.key === "ArrowRight") go(1)
      else if (e.key === "ArrowLeft") go(-1)
    }
    window.addEventListener("keydown", key)
    return () => window.removeEventListener("keydown", key)
  }, [go, onClose])
  const cur = list[Math.min(i, list.length - 1)]
  if (!cur) return null
  const nav = "grid size-10 place-items-center rounded-full bg-white/10 text-white/90 ring-1 ring-white/15 backdrop-blur-md transition-colors hover:bg-white/20"
  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="Images" onClick={onClose}
      className="fixed inset-0 z-[100] flex animate-in flex-col items-center justify-center bg-black/80 px-4 py-6 backdrop-blur-xl fade-in duration-200">
      <button onClick={onClose} aria-label="Close" className={cn(nav, "absolute right-4 top-4 size-9")}><X className="size-4" /></button>
      <div className="flex w-full max-w-5xl flex-1 items-center gap-3" onClick={(e) => e.stopPropagation()}>
        {list.length > 1 && <button className={nav} onClick={() => go(-1)} aria-label="Previous image"><ChevronLeft className="size-5" /></button>}
        <div className="flex min-w-0 flex-1 flex-col items-center gap-3">
          <img key={cur.src} src={cur.src} alt={label(cur)} referrerPolicy="no-referrer" draggable={false}
            className="max-h-[72vh] max-w-full animate-in rounded-[var(--radius-panel)] object-contain shadow-2xl ring-1 ring-white/10 fade-in zoom-in-95 duration-300" />
          <div className="flex w-full max-w-2xl items-center gap-3 rounded-[var(--radius-panel)] bg-white/[0.06] px-3 py-2 ring-1 ring-white/10 backdrop-blur-md">
            <img src={favicon(cur.page)} alt="" referrerPolicy="no-referrer" className="size-5 shrink-0 rounded" onError={hideBroken} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-ui font-medium text-white/95">{label(cur)}</div>
              <div className="truncate text-meta text-white/55">{cur.publisher || host(cur.page)}{cur.cited ? " · cited in the answer" : ""}</div>
            </div>
            <span className="shrink-0 text-meta tabular-nums text-white/45">{i + 1} / {list.length}</span>
            <a href={cur.page} target="_blank" rel="noreferrer"
              className="flex shrink-0 items-center gap-1 rounded-full bg-white px-3 py-1 text-meta font-medium text-black transition-opacity hover:opacity-85">
              Open page <ArrowUpRight className="size-3.5" />
            </a>
          </div>
        </div>
        {list.length > 1 && <button className={nav} onClick={() => go(1)} aria-label="Next image"><ChevronRight className="size-5" /></button>}
      </div>
      {list.length > 1 && (
        <div className="mt-3 flex max-w-full gap-2 overflow-x-auto pb-1" onClick={(e) => e.stopPropagation()}>
          {list.map((im, j) => (
            <button key={im.src} onClick={() => setI(j)} aria-label={`Image ${j + 1}`}
              className={cn("h-12 w-[72px] shrink-0 overflow-hidden rounded-lg ring-2 transition-[opacity,box-shadow] duration-(--dur-fast)",
                j === i ? "opacity-100 ring-white/80" : "opacity-50 ring-transparent hover:opacity-90")}>
              <img src={im.src} alt="" referrerPolicy="no-referrer" draggable={false} className="size-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>,
    document.body)
}

/** The visual widget at the top of a search answer: a rail of cards, one per page the agent read that has a picture
 *  — places first (named from the site itself), articles after, cited pages first. Cards open a full-screen viewer
 *  with a link to the page. Images that fail to load drop out; the widget disappears if none load. */
export function ImageCarousel({ images }: { images: AnswerImage[] }) {
  const [broken, setBroken] = useState<Set<string>>(() => new Set())
  const [open, setOpen] = useState<number | null>(null)
  const rail = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  const list = images.filter((im) => !broken.has(im.src))
    .map((im, k) => ({ im, k }))
    .sort((a, b) => Number(!!a.im.listing) - Number(!!b.im.listing) || Number(!a.im.cited) - Number(!b.im.cited) || a.k - b.k)
    .map(({ im }) => im)
  const measure = useCallback(() => {
    const el = rail.current
    if (el) setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 })
  }, [])
  useEffect(() => {
    measure()
    const el = rail.current
    if (!el) return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure, list.length])
  if (!list.length) return null
  const places = list.filter((im) => !im.listing).length
  const scroll = (d: number) => rail.current?.scrollBy({ left: d * (rail.current.clientWidth * 0.8), behavior: "smooth" })
  const arrow = "absolute top-1/2 z-10 grid size-8 -translate-y-1/2 place-items-center rounded-full bg-surface-1/90 text-foreground " +
    "shadow-lg ring-1 ring-line-2 backdrop-blur-md transition-opacity hover:bg-surface-2"
  return (
    <div className="not-prose mb-4 max-w-3xl animate-in fade-in slide-in-from-bottom-1 duration-500">
      <div className="mb-2 flex items-center gap-1.5 text-meta text-muted-foreground">
        <Images className="size-3.5" />
        <span className="label-tech">{places ? (places === 1 ? "1 place" : `${places} places`) : "From the web"}</span>
        {places > 0 && list.length > places && <span className="text-muted-foreground/60">· {list.length - places} more from articles</span>}
      </div>
      <div className="group/rail relative">
        <div ref={rail} onScroll={measure}
          className="flex snap-x snap-mandatory gap-2.5 overflow-x-auto scroll-smooth pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          style={{ maskImage: `linear-gradient(to right, ${edges.left ? "transparent, black 32px" : "black, black"}, ${edges.right ? "black calc(100% - 32px), transparent" : "black, black"})` }}>
          {list.map((im, j) => (
            <Card key={im.src} im={im} onOpen={() => setOpen(j)} onBroken={() => setBroken((b) => new Set(b).add(im.src))} />
          ))}
        </div>
        {edges.left && <button className={cn(arrow, "-left-3 opacity-0 group-hover/rail:opacity-100")} onClick={() => scroll(-1)} aria-label="Scroll left"><ChevronLeft className="size-4" /></button>}
        {edges.right && <button className={cn(arrow, "-right-3 opacity-0 group-hover/rail:opacity-100")} onClick={() => scroll(1)} aria-label="Scroll right"><ChevronRight className="size-4" /></button>}
      </div>
      {open !== null && <Lightbox list={list} start={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
