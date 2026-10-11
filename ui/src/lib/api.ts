import type { Attachment, Conv, Settings, Task, Thread } from "./types"

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`)
  return data as T
}

export const api = {
  state: () =>
    call<{ settings: Settings; threads: Thread[]; models: string[]; user: string; openai: boolean }>("GET", "/api/state"),
  conv: (id: string) => call<Conv>("GET", `/api/conv/${id}`),
  send: (b: { conv_id: string | null; text: string; mode: string; attachments?: Attachment[]; model?: string; think?: boolean }) =>
    call<{ conv_id: string; result: string }>("POST", "/api/send", b),
  stop: (conv_id: string) => call("POST", "/api/stop", { conv_id }),
  approve: (call_id: string, decision: string) => call("POST", "/api/approve", { call_id, decision }),
  revert: (conv_id: string, index: number) => call<{ message: string }>("POST", "/api/revert", { conv_id, index }),
  remove: (id: string) => call("DELETE", `/api/conv/${id}`),
  rename: (id: string, title: string) => call("POST", `/api/conv/${id}/rename`, { title }),
  settings: (patch: Partial<Settings>) => call<{ settings: Settings }>("PUT", "/api/settings", patch),
  file: (path: string) =>
    call<{ path: string; text: string; size: number; mtime: number; binary: boolean; truncated: boolean; crlf: boolean; kind: string }>(
      "GET", `/api/file?path=${encodeURIComponent(path)}`),
  saveFile: (path: string, text: string, crlf: boolean) => call("POST", "/api/file", { path, text, crlf }),
  doc: (path: string) => call<{ text: string; unit: string; total: number; note: string }>("GET", `/api/doc?path=${encodeURIComponent(path)}`),
  open: (path: string, how: "default" | "reveal" = "default") => call("POST", "/api/open", { path, how }),
  tasks: () => call<{ tasks: Task[] }>("GET", "/api/tasks"),
  bgOutput: (id: string, n = 400) => call<{ lines: string[] }>("GET", `/api/bg/output?id=${encodeURIComponent(id)}&n=${n}`),
  bgStop: (id: string) => call("POST", "/api/bg/stop", { id }),
  clearTasks: () => call("POST", "/api/tasks/clear", {}),
  upload: async (files: File[]): Promise<Attachment[]> => {
    const fd = new FormData()
    files.forEach((f) => fd.append("file", f, f.name))
    const r = await fetch("/api/upload", { method: "POST", body: fd })
    const data = await r.json()
    if (!r.ok) throw new Error(data.error || "upload failed")
    return data.files
  },
}

export const rawUrl = (path: string) => `/api/file?raw=1&path=${encodeURIComponent(path)}`
export const previewUrl = (path: string, v = 0) => `/api/preview?path=${encodeURIComponent(path)}&v=${v}`
