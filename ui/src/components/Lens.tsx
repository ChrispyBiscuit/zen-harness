/** The lens that passes over a running chat's sidebar row (index.css .zen-lens): a clear glass sphere glides across
 *  the row, bulging what is under it, splitting it into red, green and blue at slightly different strengths (the
 *  chromatic fringe breathes as it travels), with a soft specular glint. One SVG filter, defined once and shared by
 *  every running row; the sphere's position animates inside the filter (SMIL), so React never re-renders for it.
 *
 *  Units are the row's bounding box (primitiveUnits="objectBoundingBox"): the sphere is 15% of the row's width and
 *  its full height, which is about round on a 260 × 36 sidebar row. Reduced motion: index.css drops the filter. */

const SIZE = 96

/** A displacement map for a sphere: inside the circle, R/G push each sample towards the centre (most at mid-radius,
 *  nothing at the rim or the middle), which reads as a bulging glass bead; outside, 0.5 = no displacement. */
function sphereMap() {
  const c = document.createElement("canvas")
  c.width = c.height = SIZE
  const g = c.getContext("2d")
  if (!g) return ""
  const img = g.createImageData(SIZE, SIZE)
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const nx = (x + 0.5) / SIZE * 2 - 1, ny = (y + 0.5) / SIZE * 2 - 1
      const r2 = nx * nx + ny * ny
      const k = r2 < 1 ? (1 - r2) * Math.sqrt(1 - r2) * 1.6 : 0     // bulge profile, soft at the rim
      const i = (y * SIZE + x) * 4
      img.data[i] = Math.round(255 * Math.min(1, Math.max(0, 0.5 - 0.5 * nx * k)))
      img.data[i + 1] = Math.round(255 * Math.min(1, Math.max(0, 0.5 - 0.5 * ny * k)))
      img.data[i + 2] = 128
      img.data[i + 3] = 255
    }
  }
  g.putImageData(img, 0, 0)
  return c.toDataURL()
}

/** The glass itself: clear in the middle, a bright glint up and to the left, a thin rim catching the light underneath. */
function glintMap() {
  const c = document.createElement("canvas")
  c.width = c.height = SIZE
  const g = c.getContext("2d")
  if (!g) return ""
  const m = SIZE / 2
  const body = g.createRadialGradient(m, m, 0, m, m, m)
  body.addColorStop(0, "rgba(255,255,255,0)")
  body.addColorStop(0.8, "rgba(255,255,255,0.008)")
  body.addColorStop(0.95, "rgba(255,255,255,0.09)")
  body.addColorStop(1, "rgba(255,255,255,0)")
  g.fillStyle = body
  g.beginPath(); g.arc(m, m, m, 0, Math.PI * 2); g.fill()
  const glint = g.createRadialGradient(m * 0.62, m * 0.55, 0, m * 0.62, m * 0.55, m * 0.42)
  glint.addColorStop(0, "rgba(255,255,255,0.22)")
  glint.addColorStop(1, "rgba(255,255,255,0)")
  g.fillStyle = glint
  g.beginPath(); g.arc(m, m, m, 0, Math.PI * 2); g.fill()
  return c.toDataURL()
}

const MAP = typeof document !== "undefined" ? sphereMap() : ""
const GLINT = typeof document !== "undefined" ? glintMap() : ""

// one pass: in from the left, across, out to the right in 1.9 s; then a rest; 3.4 s a cycle
const DUR = "3.4s"
const X = { values: "-0.2;1.05;1.05", keyTimes: "0;0.56;1", keySplines: "0.45 0 0.3 1;0 0 1 1" }
const W = 0.15        // the sphere's width, as a share of the row's width

function Travel() {
  return <animate attributeName="x" dur={DUR} repeatCount="indefinite" calcMode="spline" {...X} />
}

export function LensFilter() {
  if (!MAP) return null
  return (
    <svg aria-hidden="true" width="0" height="0" style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}>
      <filter id="zen-lens" x="0" y="0" width="1" height="1" primitiveUnits="objectBoundingBox" colorInterpolationFilters="sRGB">
        <feImage href={MAP} x="-0.2" y="0" width={W} height="1" preserveAspectRatio="none" result="map"><Travel /></feImage>
        {/* outside the moving sphere the map is empty: fill it with neutral grey (no displacement) */}
        <feFlood floodColor="rgb(128,128,128)" result="grey" />
        <feComposite in="map" in2="grey" operator="over" result="field" />
        {/* the same bulge at three strengths, one per channel: the fringe. The spread breathes as it travels. */}
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.048" xChannelSelector="R" yChannelSelector="G" result="dr">
          <animate attributeName="scale" values="0.048;0.053;0.048;0.048" keyTimes="0;0.28;0.56;1" dur={DUR} repeatCount="indefinite" />
        </feDisplacementMap>
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.042" xChannelSelector="R" yChannelSelector="G" result="dg" />
        <feDisplacementMap in="SourceGraphic" in2="field" scale="0.036" xChannelSelector="R" yChannelSelector="G" result="db">
          <animate attributeName="scale" values="0.036;0.031;0.036;0.036" keyTimes="0;0.28;0.56;1" dur={DUR} repeatCount="indefinite" />
        </feDisplacementMap>
        <feColorMatrix in="dr" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
        <feColorMatrix in="dg" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
        <feColorMatrix in="db" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
        <feBlend in="r" in2="g" mode="screen" result="rg" />
        <feBlend in="rg" in2="b" mode="screen" result="rgb" />
        {/* the glass on top, travelling with the bulge */}
        <feImage href={GLINT} x="-0.2" y="0" width={W} height="1" preserveAspectRatio="none" result="glint"><Travel /></feImage>
        <feComposite in="glint" in2="rgb" operator="over" />
      </filter>
    </svg>
  )
}
