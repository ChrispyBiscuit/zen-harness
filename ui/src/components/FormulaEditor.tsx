import { useEffect, useRef, useState } from "react"
import { Image as ImageIcon, Loader2, X } from "lucide-react"
import { toast } from "sonner"
import "mathlive"
import { Button } from "@/components/ui/button"

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "math-field": React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & { "math-virtual-keyboard-policy"?: string }
    }
  }
}

type MathField = HTMLElement & { value: string; focus: () => void; executeCommand?: (c: string) => void }

/** WYSIWYG formula editor (MathLive). Inserts LaTeX into the message as $…$ or $$…$$.
 *  "From image" reads a picture of an equation (paste or pick a file) and loads it here for correction. */
export function FormulaEditor({ onInsert, onClose }: { onInsert: (latex: string, block: boolean) => void; onClose: () => void }) {
  const ref = useRef<MathField | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const mf = ref.current
    if (!mf) return
    ;(mf as any).smartFence = true
    ;(mf as any).mathVirtualKeyboardPolicy = "manual"
    setTimeout(() => mf.focus(), 30)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); insert(false) }
    }
    mf.addEventListener("keydown", onKey)
    return () => mf.removeEventListener("keydown", onKey)
  }, [])

  const insert = (block: boolean) => {
    const v = (ref.current?.value || "").trim()
    if (!v) return onClose()
    onInsert(v, block)
  }

  const fromImage = async (file: File) => {
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append("image", file)
      const r = await fetch("/api/formula_ocr", { method: "POST", body: fd }).then((x) => x.json())
      if (r.error) throw new Error(r.error)
      if (ref.current) ref.current.value = r.latex
      toast.success("Formula read from the image — check it, then insert")
    } catch (e: any) {
      toast.error(`Could not read the formula: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="absolute inset-x-2 bottom-full z-30 mb-2 rounded-panel border border-line-2 bg-surface-2 p-3 shadow-elev-2"
      onPaste={(e) => {
        const f = Array.from(e.clipboardData.files).find((x) => x.type.startsWith("image/"))
        if (f) { e.preventDefault(); fromImage(f) }
      }}>
      <div className="mb-2 flex items-center gap-2 text-caption text-muted-foreground">
        <span className="font-medium text-foreground">Formula</span>
        <span>type math naturally: / for fractions, ^ for powers, \sqrt, \sum…; paste a picture of an equation to read it</span>
        <button onClick={onClose} className="ml-auto rounded p-1 hover:bg-surface-2"><X className="size-3.5" /></button>
      </div>
      <math-field ref={ref as any} className="block w-full rounded-lg border border-input bg-background px-3 py-2 text-display text-foreground" />
      <div className="mt-2 flex items-center gap-2">
        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) fromImage(f); e.target.value = "" }} />
        <Button variant="ghost" size="sm" onClick={() => fileRef.current?.click()} disabled={busy}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ImageIcon className="size-3.5" />} From image
        </Button>
        <Button variant="ghost" size="sm" onClick={() => (ref.current as any)?.executeCommand?.("toggleVirtualKeyboard")}>Keyboard</Button>
        <div className="ml-auto flex gap-1.5">
          <Button variant="outline" size="sm" onClick={() => insert(true)}>Insert as block</Button>
          <Button size="sm" onClick={() => insert(false)}>Insert <span className="ml-1 text-meta opacity-70">Ctrl+Enter</span></Button>
        </div>
      </div>
    </div>
  )
}
