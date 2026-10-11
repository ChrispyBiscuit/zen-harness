import fs from "fs"
import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"

/** `vite --mode replay`: serves replays/*.json and the images they point at, and answers every other /api request
 *  itself, so a replay never reaches the backend (127.0.0.1:8765) or the model server. See src/lib/replay.ts. */
function replayServer(): Plugin {
  const dir = path.resolve(import.meta.dirname, "replays")
  return {
    name: "replay-server",
    configureServer(server) { server.middlewares.use(serve) },
    configurePreviewServer(server) { server.middlewares.use(serve) },   // perf runs against a production build
  }
  function serve(req: any, res: any, next: () => void) {
    const url = new URL(req.url || "/", "http://x")
    const send = (status: number, type: string, body: Buffer | string) => {
      res.statusCode = status
      res.setHeader("Content-Type", type)
      res.setHeader("Cache-Control", "no-store")
      res.end(body)
    }
    if (url.pathname.startsWith("/replays/")) {
      const f = path.join(dir, path.basename(url.pathname))
      return fs.existsSync(f) ? send(200, "application/json", fs.readFileSync(f)) : send(404, "text/plain", "no replay")
    }
    if (url.pathname === "/api/file" && url.searchParams.get("raw") === "1") {
      const p = url.searchParams.get("path") || ""
      const f = path.join(dir, "files", path.basename(p.replace(/^replay:\/\//, "")))
      return p.startsWith("replay://") && fs.existsSync(f) ? send(200, "image/png", fs.readFileSync(f))
        : send(404, "text/plain", "not in replay")
    }
    if (url.pathname.startsWith("/api") || url.pathname === "/ws") return send(503, "application/json",
      JSON.stringify({ error: "replay mode: no backend" }))
    next()
  }
}

// The app is served by the Python backend (backend/web_backend.py); build output goes to backend/web_dist,
// or wherever ZEN_UI_OUT points (e.g. an older checkout).
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), ...(mode === "replay" ? [replayServer()] : [])],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  base: "./",
  build: {
    outDir: process.env.ZEN_UI_OUT || path.resolve(import.meta.dirname, "../backend/web_dist"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
  preview: { port: 5181, strictPort: true },
  server: mode === "replay" ? { port: 5180, strictPort: true }
    : { proxy: { "/api": "http://127.0.0.1:8765", "/ws": { target: "ws://127.0.0.1:8765", ws: true } } },
}))
