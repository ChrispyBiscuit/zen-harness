/** Delete with an undo window instead of a confirm box: the thing disappears at once, a toast offers Undo for a few
 *  seconds, and only when that runs out (or the toast is closed) is the delete sent. */
import { toast } from "sonner"

export function deleteWithUndo({ label, hide, restore, commit }: {
  label: string; hide: () => void; restore: () => void; commit: () => Promise<unknown> | unknown
}) {
  let undone = false, sent = false
  const send = () => {
    if (undone || sent) return
    sent = true
    Promise.resolve(commit()).catch((e: any) => { restore(); toast.error(`Couldn't delete: ${e?.message || e}`) })
  }
  hide()
  toast(label, {
    duration: 6000,
    action: { label: "Undo", onClick: () => { undone = true; restore() } },
    onAutoClose: send,
    onDismiss: send,
  })
}
