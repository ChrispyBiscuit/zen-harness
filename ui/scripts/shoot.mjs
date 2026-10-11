// Screenshot + perf baseline for the polish-v2 work, driven over the Chrome DevTools Protocol (no extra packages).
// Needs the replay dev server (`npm run replay`, port 5180): nothing here touches the backend or the model.
//
//   node scripts/shoot.mjs shots docs/polish-v2/before     -> the fixed screenshot set at 1280x800 and 800x450
//   node scripts/shoot.mjs perf                            -> JSON: streaming frame timing, DOM node counts
import { spawn } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"
const BASE = process.env.ZEN_BASE || "http://localhost:5180/"
const PORT = 9333
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function launch() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "zen-shoot-"))
  const proc = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${dir}`,
    "--hide-scrollbars", "--force-color-profile=srgb", "--no-first-run", "about:blank"], { stdio: "ignore" })
  let target
  for (let i = 0; i < 50 && !target; i++) {
    await sleep(200)
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((t) => t.type === "page") } catch { /* starting */ }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((r) => ws.addEventListener("open", r, { once: true }))
  let id = 0
  const waiting = new Map()
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data)
    if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id) }
  })
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id
    waiting.set(i, (m) => (m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result)))
    ws.send(JSON.stringify({ id: i, method, params }))
  })
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed")
    return r.result.value
  }
  const close = () => { try { ws.close() } catch { /* */ } proc.kill(); try { fs.rmSync(dir, { recursive: true, force: true }) } catch { /* */ } }
  await send("Page.enable")
  await send("Runtime.enable")
  // ZEN_PRELOAD=<file.js>: run before the app's own scripts on every page (e.g. a stand-in desktop bridge)
  if (process.env.ZEN_PRELOAD) await send("Page.addScriptToEvaluateOnNewDocument", { source: fs.readFileSync(process.env.ZEN_PRELOAD, "utf8") })
  return { send, evalJs, close }
}

async function open(b, query, w, h) {
  await b.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false })
  await b.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] })
  await b.send("Page.navigate", { url: BASE + query })
  for (let i = 0; i < 60; i++) {
    await sleep(150)
    if (await b.evalJs("!!window.__replay && document.readyState === 'complete'").catch(() => false)) break
  }
  await sleep(900)                                    // fonts, shiki, layout settle
}

async function shot(b, file) {
  const r = await b.send("Page.captureScreenshot", { format: "png" })
  fs.writeFileSync(file, Buffer.from(r.data, "base64"))
  console.log("saved", file)
}

const scrollTranscript = (frac) => `(() => { const els = [...document.querySelectorAll('div')].filter(e =>
  e.scrollHeight > e.clientHeight + 40 && getComputedStyle(e).overflowY === 'auto' && e.querySelector('.prose-chat, [class*=rounded-xl]'));
  const el = els.sort((a, b) => b.scrollHeight - a.scrollHeight)[0]; if (!el) return 0;
  el.scrollTop = (el.scrollHeight - el.clientHeight) * ${frac}; return el.scrollHeight })()`

const SHOTS = [
  { name: "empty", q: "?replay=fixture&hud=0&conv=none" },
  { name: "fixture-top", q: "?replay=fixture&hud=0", js: scrollTranscript(0) },
  { name: "fixture-mid1", q: "?replay=fixture&hud=0", js: scrollTranscript(0.33) },
  { name: "fixture-mid2", q: "?replay=fixture&hud=0", js: scrollTranscript(0.66) },
  { name: "fixture-end", q: "?replay=fixture&hud=0", js: scrollTranscript(1) },
  { name: "long-run", q: "?replay=fixture&hud=0&conv=3be98ece", js: scrollTranscript(0.5) },
  { name: "fixture-compact", q: "?replay=fixture&hud=0", js: "document.documentElement.dataset.density = 'compact'; " + scrollTranscript(0) },
  { name: "settings", q: "?replay=fixture&hud=0", js: "window.__app.setState({ settingsOpen: true })" },
  { name: "settings-appearance", q: "?replay=fixture&hud=0", js: "window.__app.setState({ settingsOpen: true, settingsTab: 'appearance' })" },
  { name: "panel-checklist", q: "?replay=fixture&hud=0", js: "window.__dock.showPanel('checklist')" },
  { name: "panel-browser", q: "?replay=fixture&hud=0", js: "window.__dock.showPanel('browser')" },
  { name: "panel-artifacts", q: "?replay=fixture&hud=0", js: "window.__dock.showPanel('artifacts')" },
  { name: "panel-tasks", q: "?replay=fixture&hud=0", js: "window.__dock.showPanel('tasks')" },
]

async function shots(outDir) {
  fs.mkdirSync(outDir, { recursive: true })
  const b = await launch()
  try {
    for (const [w, h] of [[1280, 800], [800, 450]]) {
      for (const s of SHOTS) {
        await open(b, s.q, w, h)
        if (s.js) { await b.evalJs(s.js); await sleep(700) }
        await shot(b, path.join(outDir, `${s.name}-${w}x${h}.png`))
      }
    }
    // the whole fixture in one tall image: proof that every card renders
    await open(b, "?replay=fixture&hud=0", 1280, 800)
    const full = await b.evalJs(scrollTranscript(0))
    await open(b, "?replay=fixture&hud=0", 1280, Math.min(16000, full + 220))
    await shot(b, path.join(outDir, "fixture-full-1280.png"))
  } finally { b.close() }
}

async function perf() {
  const b = await launch()
  const out = {}
  try {
    await open(b, "?replay=fixture&hud=0", 1280, 800)
    out.fixture = await b.evalJs(`({ domNodes: document.getElementsByTagName('*').length,
      messages: window.__app.getState().convs.fixture.messages.length })`)
    await open(b, "?replay=fixture&hud=0&conv=3be98ece", 1280, 800)
    out.longRun = await b.evalJs(`({ domNodes: document.getElementsByTagName('*').length,
      messages: Object.values(window.__app.getState().convs).find(c => c.id.startsWith('3be98ece')).messages.length })`)
    // streaming: play the build at 4x and record every frame interval for 60 s
    await open(b, "?replay=build-run&hud=0&speed=4", 1280, 800)
    out.stream = await b.evalJs(`new Promise((res) => {
      const d = []; let last = performance.now(); const t0 = last; let longTasks = 0, longMs = 0
      try { new PerformanceObserver((l) => { for (const e of l.getEntries()) { longTasks++; longMs += e.duration } })
        .observe({ type: 'longtask', buffered: false }) } catch {}
      const f = (t) => { d.push(t - last); last = t; if (t - t0 < 60000) requestAnimationFrame(f); else {
        d.sort((a, b) => a - b); const q = (p) => +d[Math.floor(d.length * p)].toFixed(1)
        res({ seconds: 60, speed: 4, frames: d.length, fps: +(d.length / 60).toFixed(1), p50: q(0.5), p95: q(0.95),
          p99: q(0.99), max: +d[d.length - 1].toFixed(1), over20ms: d.filter(x => x > 20).length,
          over33ms: d.filter(x => x > 33.4).length, longTasks, longTaskMs: Math.round(longMs),
          eventsPlayed: window.__replay.state().idx, domNodesAtEnd: document.getElementsByTagName('*').length,
          heapMB: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null }) } }
      requestAnimationFrame(f) })`)
  } finally { b.close() }
  console.log(JSON.stringify(out, null, 2))
}

/** The empty-state smoke plane: frames over time, frame cost, and a forced crash (dive, puff, flames, re-entry). */
async function plane(outDir) {
  fs.mkdirSync(outDir, { recursive: true })
  const b = await launch()
  try {
    await open(b, "?replay=fixture&hud=0&conv=none", 1280, 800)
    await sleep(2500)
    const grab = async (name) => {
      const r = await b.send("Page.captureScreenshot", { format: "png" })
      fs.writeFileSync(path.join(outDir, name), Buffer.from(r.data, "base64")); console.log("saved", name)
    }
    for (let k = 0; k < 6; k++) { await grab(`plane-t${k}.png`); await sleep(1500) }
    const perf = await b.evalJs(`new Promise((res) => { const d = []; let l = performance.now(); const t0 = l
      const f = (t) => { d.push(t - l); l = t; if (t - t0 < 8000) requestAnimationFrame(f); else { d.sort((a, b) => a - b)
        res({ rafFrames: d.length, p50: +d[d.length >> 1].toFixed(1), p99: +d[Math.floor(d.length * 0.99)].toFixed(1),
          stepMs: +window.__plane.frameMs.toFixed(2), state: window.__plane.state }) } }
      requestAnimationFrame(f) })`)
    console.log("perf", JSON.stringify(perf))
    await b.evalJs("window.__plane.crashSoon()")
    const at = [1200, 1200, 1100, 300, 500, 900, 2500, 3000]
    for (let k = 0; k < at.length; k++) {
      await sleep(at[k]); await grab(`crash-${k}.png`)
      console.log("crash", k, JSON.stringify(await b.evalJs("window.__plane.state")))
    }
  } finally { b.close() }
}

/** Open/close motion: trigger each, list the animations running, grab a mid-flight frame, check the end state. */
async function motion(outDir) {
  fs.mkdirSync(outDir, { recursive: true })
  const b = await launch()
  const out = {}
  const anims = `document.getAnimations().map(a => a.animationName || "waapi").filter((v, i, xs) => xs.indexOf(v) === i)`
  const step = async (name, js, check) => {
    await b.evalJs(js)
    await sleep(70)
    const running = await b.evalJs(anims)
    const r = await b.send("Page.captureScreenshot", { format: "png" })
    fs.writeFileSync(path.join(outDir, `${name}.png`), Buffer.from(r.data, "base64"))
    await sleep(600)
    out[name] = { running, after: check ? await b.evalJs(check) : undefined }
  }
  try {
    await open(b, "?replay=fixture&hud=0", 1280, 800)
    await sleep(800)
    await step("dialog-open", "window.__app.setState({ settingsOpen: true })", "!!document.querySelector('[data-slot=dialog-content]')")
    await step("dialog-close", "window.__app.setState({ settingsOpen: false })", "!document.querySelector('[data-slot=dialog-content]')")
    await step("sidebar-close", "window.__app.setState({ sidebarOpen: false })", "!document.querySelector('aside')")
    await step("sidebar-open", "window.__app.setState({ sidebarOpen: true })", "!!document.querySelector('aside')")
    await step("panel-close", "window.__dock.hidePanel('checklist'); window.__dock.hidePanel('browser'); window.__dock.hidePanel('tasks'); window.__dock.hidePanel('artifacts')",
      "['checklist','browser','tasks','artifacts'].filter(id => window.__dock.isVisible(id))")
    await step("panel-open", "window.__dock.showPanel('checklist')", "window.__dock.isVisible('checklist')")
    await step("tab-switch", "window.__dock.showPanel('tasks')", "window.__dock.isVisible('tasks')")
    await step("float", "window.__dock.floatPanel('tasks')", "!!document.querySelector('.dv-resize-container')")
  } finally { b.close() }
  console.log(JSON.stringify(out, null, 1))
}

/** Run a JS file in the replay page (async allowed: the file's body is awaited) and print the result;
 *  `node scripts/shoot.mjs eval file.js [shot.png] [query]` */
async function evalFile(file, png, query) {
  const b = await launch()
  try {
    await open(b, query || "?replay=fixture&hud=0", 1280, 800)
    const body = fs.readFileSync(file, "utf8")
    const r = await b.evalJs(`(async () => { ${body} })()`)
    console.log(JSON.stringify(r, null, 1))
    if (png) { await sleep(500); const s = await b.send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(png, Buffer.from(s.data, "base64")) }
  } finally { b.close() }
}

/** Full-length screenshots of saved chats: `node scripts/shoot.mjs chats <outdir> id1,id2,...` (replays/history.json).
 *  The window is grown to the transcript's height so one image shows the whole conversation (capped). */
async function chats(outDir, ids) {
  fs.mkdirSync(outDir, { recursive: true })
  const b = await launch()
  try {
    for (const id of ids) {
      await open(b, `?replay=history&hud=0&conv=${id}`, 1180, 900)
      await sleep(600)
      const h = await b.evalJs(`(() => { const e = [...document.querySelectorAll("div.overflow-y-auto")].find(x => x.querySelector("[data-density-scope]")); return e ? e.scrollHeight : 900 })()`)
      await open(b, `?replay=history&hud=0&conv=${id}`, 1180, Math.min(14000, h + 220))
      await sleep(900)
      await b.evalJs(`(() => { const e = [...document.querySelectorAll("div.overflow-y-auto")].find(x => x.querySelector("[data-density-scope]")); if (e) e.scrollTop = 0; return 1 })()`)
      await sleep(300)
      const r = await b.send("Page.captureScreenshot", { format: "png" })
      fs.writeFileSync(path.join(outDir, `chat-${id}.png`), Buffer.from(r.data, "base64"))
      console.log("saved", id, h)
    }
  } finally { b.close() }
}

const [cmd, arg, arg2, arg3] = process.argv.slice(2)
if (cmd === "chats") await chats(arg, (arg2 || "").split(",").filter(Boolean))
else if (cmd === "eval") await evalFile(arg, arg2, arg3)
else if (cmd === "motion") await motion(arg || "docs/polish-v2/motion")
else if (cmd === "plane") await plane(arg || "docs/polish-v2/plane")
else if (cmd === "shots") await shots(arg || "docs/polish-v2/shots")
else if (cmd === "perf") await perf()
else console.log("usage: node scripts/shoot.mjs shots <dir> | perf")
