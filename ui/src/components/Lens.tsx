/** The effect that passes over a running chat's sidebar row (index.css .zen-refract), in place of a plain shimmer:
 *  a soft band sweeps across the whole row, and inside it the field itself bends (a gentle bulge, strongest at the
 *  band's middle, fading to nothing at its edges), splits into red, green and blue at slightly different strengths
 *  (the fringe breathes as it travels), and lifts a touch in brightness. No visible shape: just the row warping as
 *  the band goes by. One SVG filter, defined once and shared by every running row; the band's position animates
 *  inside the filter (SMIL), so React never re-renders for it.
 *
 *  Units are the row's bounding box (primitiveUnits="objectBoundingBox"): the band is 30% of the row's width and its
 *  full height. Reduced motion: index.css drops the filter. */

const SIZE = 96

/** The displacement field across the band: R bends samples towards the band's centre line (a horizontal bulge,
 *  soft to zero at both edges), G a little towards the row's middle (so it reads as rounded, not flat). 0.5 = none. */
function bandMap() {
  const c = document.createElement("canvas")
  c.width = c.height = SIZE
  const g = c.getContext("2d")
  if (!g) return ""
  const img = g.createImageData(SIZE, SIZE)
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const nx = (x + 0.5) / SIZE * 2 - 1, ny = (y + 0.5) / SIZE * 2 - 1
      const k = Math.pow(1 - nx * nx, 1.5) * 1.5                    // strongest mid-band, zero at its edges
      const i = (y * SIZE + x) * 4
      img.data[i] = Math.round(255 * Math.min(1, Math.max(0, 0.5 - 0.5 * nx * k)))
      img.data[i + 1] = Math.round(255 * Math.min(1, Math.max(0, 0.5 - 0.22 * ny * k)))
      img.data[i + 2] = 128
      img.data[i + 3] = 255
    }
  }
  g.putImageData(img, 0, 0)
  return c.toDataURL()
}

/** A faint lift of light across the band (the shimmer's warmth, kept): a soft vertical gradient, no edges. */
function lightMap() {
  const c = document.createElement("canvas")
  c.width = c.height = SIZE
  const g = c.getContext("2d")
  if (!g) return ""
  const l = g.createLinearGradient(0, 0, SIZE, 0)
  l.addColorStop(0, "rgba(255,255,255,0)")
  l.addColorStop(0.5, "rgba(255,250,240,0.035)")
  l.addColorStop(1, "rgba(255,255,255,0)")
  g.fillStyle = l
  g.fillRect(0, 0, SIZE, SIZE)
  return c.toDataURL()
}

const MAP = typeof document !== "undefined" ? bandMap() : ""
const LIGHT = typeof document !== "undefined" ? lightMap() : ""

// a 6 s cycle: glide in from the left and ease to a stop three quarters across (2.3 s), hold (0.5 s), slide back out
// to the left (2 s), rest off the row (1.2 s)
const DUR = "6s"
const X = { values: "-0.32;0.72;0.72;-0.32;-0.32", keyTimes: "0;0.38;0.46;0.8;1",
  keySplines: "0.16 1 0.3 1;0 0 1 1;0.65 0 0.35 1;0 0 1 1" }
const W = 0.3         // the band's width, as a share of the row's width

function Travel() {
  return <animate attributeName="x" dur={DUR} repeatCount="indefinite" calcMode="spline" {...X} />
}

export function RefractFilter() {
  if (!MAP) return null
  return (
    <svg aria-hidden="true" width="0" height="0" style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}>
      <filter id="zen-refract" x="0" y="0" width="1" height="1" primitiveUnits="objectBoundingBox" colorInterpolationFilters="sRGB">
        <feImage href={MAP} x="-0.32" y="0" width={W} height="1" preserveAspectRatio="none" result="map"><Travel /></feImage>
        {/* outside the moving band the map is empty: fill it with neutral grey (no displacement) */}
        <feFlood floodColor="rgb(128,128,128)" result="grey" />
        <feComposite in="map" in2="grey" operator="over" result="field" />
        {/* the same bulge at three strengths, one per channel: the fringe. The spread breathes as it travels. */}
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.03" xChannelSelector="R" yChannelSelector="G" result="dr">
          <animate attributeName="scale" values="0.03;0.033;0.03;0.03" keyTimes="0;0.19;0.46;1" dur={DUR} repeatCount="indefinite" />
        </feDisplacementMap>
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.027" xChannelSelector="R" yChannelSelector="G" result="dg" />
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.024" xChannelSelector="R" yChannelSelector="G" result="db">
          <animate attributeName="scale" values="0.024;0.021;0.024;0.024" keyTimes="0;0.19;0.46;1" dur={DUR} repeatCount="indefinite" />
        </feDisplacementMap>
        <feColorMatrix in="dr" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
        <feColorMatrix in="dg" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
        <feColorMatrix in="db" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
        <feBlend in="r" in2="g" mode="screen" result="rg" />
        <feBlend in="rg" in2="b" mode="screen" result="rgb" />
        {/* a faint lift of light, travelling with the bend */}
        <feImage href={LIGHT} x="-0.32" y="0" width={W} height="1" preserveAspectRatio="none" result="light"><Travel /></feImage>
        <feComposite in="light" in2="rgb" operator="atop" />
      </filter>
    </svg>
  )
}
