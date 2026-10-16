import { useEffect, useRef, useState } from "react"
import mermaid from "mermaid"
import { CodeBlock } from "../CodeBlock"
import { Frame } from "./Frame"

/* ```mermaid inside an answer: drawn in place (bundled, loaded only when an answer has one; no CDN). */

let ready = false
let seq = 0

export default function MermaidBlock({ code }: { code: string }) {
  const [svg, setSvg] = useState("")
  const [err, setErr] = useState("")
  const id = useRef(`zen-mmd-${++seq}`)
  useEffect(() => {
    if (!ready) {
      mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict", fontFamily: "Geist Variable, Segoe UI, sans-serif",
        themeVariables: { background: "transparent", primaryColor: "#2a2c31", primaryBorderColor: "#4a4d55", lineColor: "#8a8f99", primaryTextColor: "#e8e9ec", fontSize: "14px" } })
      ready = true
    }
    let off = false
    mermaid.render(id.current, code.replace(/[\uE000\uE001]/g, ""))
      .then((r) => { if (!off) { setSvg(r.svg); setErr("") } })
      .catch((e) => { if (!off) setErr(String(e?.message || e).split("\n")[0]) })
    return () => { off = true }
  }, [code])
  if (err) return <div><CodeBlock code={code} lang="mermaid" /><div className="-mt-1 mb-2 text-meta text-faint">Couldn't draw this diagram: {err}</div></div>
  return (
    <Frame>
      <div className="mermaid-out flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />
    </Frame>
  )
}
