import { useRef, useState } from "react"
import { Download, Trash2, Upload } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useApp } from "@/lib/store"
import { Field, Section } from "@/components/SettingsDialog"

/** Every chat at once: import a Claude data export, export in the same format, or delete them all.
 *  Bulk changes copy conversations.json into agent_trash first (backend: /api/chats/*, claude_export.py). */
export function ChatsPanel() {
  const threads = useApp((s) => s.threads)
  const [busy, setBusy] = useState("")
  const [replace, setReplace] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  // claude.ai serves export files only to a signed-in browser: open them there, then import the zips
  const openLinks = (links?: string[]) => links?.length
    ? { action: { label: "Open in browser", onClick: () => { api.openExportLinks(links).catch((e: any) => toast.error(e.message)) } } }
    : {}
  const afterWipe = () => { const st = useApp.getState(); if (st.current) st.select(null) }

  const doImport = async (files: File[]) => {
    if (!files.length) return
    if (replace && !confirm(`Replace all ${threads.length} chats with the imported ones? A backup copy is kept in agent_trash.`)) return
    setBusy("import")
    try {
      const r = await api.importChats(files, replace)
      if (replace) afterWipe()
      toast.success(`Imported ${r.imported} chat${r.imported === 1 ? "" : "s"}`,
        { description: r.skipped ? `${r.skipped} already here, skipped.` : undefined })
      if (r.failed?.length) toast.warning("Some export files didn't download", { description: r.failed.join("; "), ...openLinks(r.links) })
    } catch (e: any) { toast.error(e.message, { duration: 20000, ...openLinks(e.links) }) }
    finally { setBusy(""); if (input.current) input.current.value = "" }
  }

  const doExport = async () => {
    setBusy("export")
    try {
      const r = await api.exportChats()
      toast.success(`Exported ${r.count} chats`, { description: r.path,
        action: { label: "Show", onClick: () => api.open(r.path, "reveal") } })
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy("") }
  }

  const doDelete = async () => {
    if (!confirm(`Delete all ${threads.length} chats? A backup copy is kept in agent_trash.`)) return
    setBusy("delete")
    try {
      const r = await api.deleteAllChats()
      afterWipe()
      toast.success("All chats deleted", r.backup ? { description: `Backup: ${r.backup}`,
        action: { label: "Show", onClick: () => api.open(r.backup, "reveal") } } : undefined)
    } catch (e: any) { toast.error(e.message) }
    finally { setBusy("") }
  }

  return (
    <Section title={`Chats (${threads.length})`}>
      <Field label="Import" hint={<>Claude's data export: pick its <span className="mono">manifest-*.json</span> (the chats
        are downloaded from its links, which expire after 24 hours), the <span className="mono">conversations-*.zip</span> files,
        or <span className="mono">conversations.json</span>. Chats already here are skipped.</>}>
        <input ref={input} type="file" multiple accept=".zip,.json,.jsonl" className="hidden"
          onChange={(e) => doImport(Array.from(e.target.files || []))} />
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="outline" disabled={!!busy} onClick={() => input.current?.click()}>
            <Upload /> {busy === "import" ? "Importing…" : "Choose files…"}
          </Button>
          <label className="flex cursor-pointer items-center gap-1.5 text-caption text-muted-foreground">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            Replace all current chats
          </label>
        </div>
      </Field>
      <Field label="Export" hint="Every chat as a zip in Claude's export format (conversations.json), saved to Downloads.">
        <Button size="sm" variant="outline" disabled={!!busy || !threads.length} onClick={doExport}>
          <Download /> {busy === "export" ? "Exporting…" : "Export all chats"}
        </Button>
      </Field>
      <Field label="Delete" hint="Removes every chat. A copy of conversations.json goes to agent_trash first.">
        <Button size="sm" variant="destructive" disabled={!!busy || !threads.length} onClick={doDelete}>
          <Trash2 /> {busy === "delete" ? "Deleting…" : "Delete all chats"}
        </Button>
      </Field>
    </Section>
  )
}
