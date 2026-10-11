# Polish v2: baseline (before Phase 1)

Measured 2026-10-09 on the laptop (RTX 3070 Laptop, Ryzen 9 5900HX), headless Chrome, 1280×800, dark.
No backend and no model were running: everything comes from the replay files (`replays/`).

How to reproduce:

```bash
npm run replays:make     # rebuild replays/ from web-agent/conversations.json (read-only)
npm run perf:build       # production build in replay mode -> .perf-dist (never web_dist)
npm run perf:serve       # vite preview on :5181 with the replay middleware
ZEN_BASE=http://localhost:5181/ node scripts/shoot.mjs perf
node scripts/bundle-size.mjs <dist>
```

## Rendering

| Measure | Value |
|---|---|
| Fixture chat (45 messages, every card type): DOM nodes | **1,360** |
| Long run (the 22-step / 28-call build, 51 messages): DOM nodes | **1,316** |
| Streaming replay, 60 s of the build at 4×: frames | 3,575 (59.6 fps) |
| Frame interval p50 / p95 / p99 | 16.7 / 16.8 / 16.9 ms |
| Frames over 20 ms / over 33 ms | 5 / 4 (max 300 ms: the page's first paint) |
| Long tasks (>50 ms) | 1 (55 ms) |
| JS heap after 60 s | 39.8 MB |

The dev server (unminified React, StrictMode) is much noisier: p99 66.7 ms, 51 long tasks in the same 60 s. Compare
production builds only.

## Bundle (`web_dist` as deployed before this work, gzip -9)

| | Raw | Gzip |
|---|---|---|
| JS, 33 chunks | 5,378.5 KB | 1,186.2 KB |
| CSS | 289.3 KB | 38.5 KB |
| Main chunk `index-*.js` | 3,137.7 KB | 921.9 KB |
| Largest lazy chunk (`cpp`, shiki grammar) | 778.3 KB | 52.9 KB |

Budget for the whole of polish v2: at most +60 KB gzip.

## Screenshots

`before/`: empty state, fixture chat (top, two middles, end), long run, settings, and each side panel
(checklist, browser, artifacts, tasks) at 1280×800 and 800×450, plus `fixture-full-1280.png` (the whole fixture
chat in one image). They already carry the Zen Harness name and ensō (the rebrand came first by decision), but no
Phase 1 tokens.

Visible problems at 800 px (for Phase 2): the chat title is clipped to "New ch…"/"F", the suggestion cards on the
empty state wrap one word per line, and the side dock squeezes the chat to ~290 px.

## After Phase 1 (rebrand + tokens), same method

| Measure | Before | After |
|---|---|---|
| Fixture DOM nodes | 1,360 | 1,365 |
| Long run DOM nodes | 1,316 | 1,321 |
| Streaming 60 s at 4×: fps / p99 / over 33 ms | 59.6 / 16.9 ms / 4 | 59.9 / 16.9 ms / 3 |
| Long tasks | 1 (55 ms) | 0 |
| JS gzip (all chunks) | 1,186.2 KB | 1,194.1 KB (+7.9 KB: ensō paths, replay player, tokens) |
| CSS gzip | 38.5 KB | 38.8 KB |

Also removed: every `backdrop-filter` in the app shell (top bar, dialog overlay, composer drop overlay, empty-state
cards) and the 28 px CSS blur on the empty-state glow.

Screenshots: `after/` (same set, plus `fixture-compact-*` and `settings-appearance-*`); `before-after.png` puts
the fixture top, fixture end and empty state side by side.
