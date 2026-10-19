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
  importChats: async (files: File[], replace: boolean) => {
    const fd = new FormData()
    fd.append("replace", replace ? "1" : "0")
    files.forEach((f) => fd.append("file", f, f.name))
    const r = await fetch("/api/chats/import", { method: "POST", body: fd })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(data.error || "import failed")
    return data as { imported: number; skipped: number; backup: string }
  },
  exportChats: (ids?: string[]) => call<{ path: string; count: number }>("POST", "/api/chats/export", { ids }),
  deleteAllChats: () => call<{ backup: string }>("POST", "/api/chats/delete_all", {}),
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

// ---- Help / About (backend: help_api.py) and Settings → Models (backend: models_api.py)
export type DocPage = { slug: string; title: string }
export type About = {
  name: string; version: string; frozen: boolean; llama_cpp_found: boolean; python: string; windows: string
  ram_mib: number | null; folders: Record<"install" | "data" | "models" | "logs" | "llama", string>
  gpu: { name: string; vram_mib: number; vram_used_mib: number; driver: string } | null
}
export type ModelFile = {
  path: string; name: string; size: number; quant: string; architecture: string; layers: number | null
  experts: number | null; helper: boolean; kind: "model" | "drafter" | "vision"
}
export type Profile = Record<string, any> & { model_file: string; num_ctx?: number; kv_cache?: string; n_cpu_moe?: number }
export type ModelList = {
  models_dir: string; files: ModelFile[]; default: string; gpu_mib: number; ram_mib: number
  profiles: Record<string, { profile: Profile; exists: boolean; path: string }>
}
export type Proposal = {
  name: string; profile: Profile; info: ModelFile; notes: string[]; gpu_mib: number
  helpers: { mmproj: string | null; drafter: string | null }; fit: Record<string, any>
}
export type TestResult = {
  ok: boolean; error?: string; load_s?: number; tokens_per_s?: number; prompt_tokens_per_s?: number
  vram_mib?: number | null; sample?: string
}
export type Download = { id: string; url: string; path: string; total: number; done: number; status: string; error?: string }

export type LicenseComponent = { id: string; name: string; version: string; kind: string; license: string; url: string; text: string }

export const help = {
  licenses: () => call<{ app: { name: string; version: string; license: string }; components: LicenseComponent[]; generated: boolean }>(
    "GET", "/api/help/licenses"),
  licenseText: (file: string) => call<{ text: string }>("GET", `/api/help/license_text?file=${encodeURIComponent(file)}`),
  docs: () => call<{ pages: DocPage[]; version: string }>("GET", "/api/help/docs"),
  doc: (slug: string) => call<{ slug: string; title: string; markdown: string }>("GET", `/api/help/doc/${slug}`),
  about: () => call<About>("GET", "/api/help/about"),
  open: (which: string) => call("POST", "/api/help/open", { which }),
  diagnostics: () => call<{ text: string }>("GET", "/api/help/diagnostics"),
}

export const modelsApi = {
  list: () => call<ModelList>("GET", "/api/models/list"),
  inspect: (path: string, ctx?: number) => call<Proposal>("POST", "/api/models/inspect", { path, ctx }),
  save: (name: string, profile: Profile, make_default = false) =>
    call<{ ok: boolean; name: string }>("POST", "/api/models/save", { name, profile, make_default }),
  remove: (name: string) => call("POST", "/api/models/delete", { name }),
  test: (profile: Profile) => call<TestResult>("POST", "/api/models/test", { profile }),
  pick: () => call<{ path: string }>("POST", "/api/models/pick", {}),
  download: (url: string) => call<{ ok: boolean; id: string }>("POST", "/api/models/download", { url }),
  cancel: (id: string) => call("POST", "/api/models/download/cancel", { id }),
  downloads: () => call<{ downloads: Download[] }>("GET", "/api/models/downloads"),
}

export const rawUrl =(path: string) => `/api/file?raw=1&path=${encodeURIComponent(path)}`
export const previewUrl = (path: string, v = 0) => `/api/preview?path=${encodeURIComponent(path)}&v=${v}`
