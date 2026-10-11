# Graphical polish v2: plan (and rename to Zen Harness)

**Goal:** lift the whole Power Harness UI (`%USERPROFILE%/dev/lws-web`) a few levels in polish, dynamics,
interactivity and design, without making it heavier, slower or less stable in the embedded window.

**Character to aim for:** a quiet, precise instrument. The dark engineering look with one cyan→violet accent
(`--brand-a` / `--brand-b`), Geist and Geist Mono, and `label-tech` labels is the right base: v2 refines it rather
than replacing it. Motion explains what changed. Colour means state. Density is a choice the user makes.

---

## 0. Where it stands (audit)

Taken from the code and from screenshots of the running app at an 800 px wide pane.

| Area | What works | What holds it back |
|---|---|---|
| **Top bar** | Model chip, context gauge, GPU/VRAM/tok/s/elapsed meters | 11 controls in one row. At 800 px "LAST RUN" wraps, "Tasks" is clipped and the meters crowd the title. No hierarchy between status and actions. |
| **Transcript** | Typewriter and scramble effects, thinking blocks, fact-check, interpretation and plan cards, files-changed card, turn stats | A long run is a wall of equally loud cards (the latest build ran 22 steps and 28 tool calls). There is no run-level summary or timeline, and old steps never fold away. The final answer looks the same as any other message, and "(stopped)" is plain text. |
| **Tool cards** | One `CardShell`. Good terminal, diff, python, table and math cards. Web-hits card | Pending, done and failed states change abruptly. The background search card is a generic row. Approvals don't stand out and have no keyboard path. |
| **New agent features** | They work end to end | No UI yet for: `ask_user` (questions arrive as plain markdown), the checklist's `design` and `done_when`, browser console errors, archive entries behind compaction stubs. |
| **Sidebar** | Grouped by date, active section, spinner on running chats | Many near-identical truncated titles ("is chicago the capital of illin…" ×6). No pinning, inline rename, unread state, preview or keyboard navigation. |
| **Composer** | Modes, plan-budget chip, mic, formula, slash menu | Dense row of small controls. The send/stop states and queued "reads it at the next step" messages are not visible as objects. |
| **Panels** (dockview) | Checklist, browser, tasks, artifacts, resizable | Panel chrome is stock dockview. Empty states are text only. The checklist is a plain list. |
| **Theme** | Carefully tuned dark tokens | The app always forces dark. The light tokens are stock shadcn greys. There is no elevation scale, so cards, panels and popovers sit on near-identical greys. |
| **Motion** | Meters transition; reduced-motion is checked for the typewriter | Almost no enter/exit or layout motion; panels and cards pop in. |
| **Stability** | n/a | The embedded Qt WebEngine window has crashed before (access violation in `Qt6WebEngineCore`). Heavy blur, `backdrop-filter` layers and very long DOM lists are risks. |

---

## 1. Principles and budgets (apply to every phase)

1. **Hierarchy first:** each screen has one primary thing. In the transcript it is the latest answer or the action
   that needs the user. Everything else steps back (lower contrast, smaller, collapsed).
2. **Colour = state:** info is working, success is done, warning needs attention, destructive is failed. The brand
   gradient is for identity and focus only, never for state.
3. **Motion explains change:** something appears where it came from, collapses where it goes, and numbers tick.
   No decorative loops except "working" indicators.
4. **Budgets:**
   - **Bundle:** at most +60 KB gzip in total.
   - **Streaming:** no dropped frames while a reply streams at 70 tok/s.
   - **Transcript:** under 1,500 DOM nodes visible at once.
   - **Reduced motion:** honoured everywhere.
5. **No backend coupling surprises:** new UI reads existing events. Where a new field is needed, the backend adds
   it in a way old UIs ignore.
6. **Develop without the GPU:** all UI work runs against a recorded-event replay (Phase 0), never the live model.

---

## 2. Phase 0: foundations for safe, fast UI work

| Item | Detail | Done when |
|---|---|---|
| **Event replay harness** | Record a real turn's event stream (`tool_call`, `tool_result`, deltas, `context`, `turn_stats`…) to JSON; a `?replay=<file>` dev mode feeds it into the store at real or 4× speed. | A recorded 22-step build replays in the browser pane with no backend model running. |
| **Fixture conversation** | One seeded chat containing every card type: terminal, diff, web hits, background search, python, table, math, tracker, interpretation, plan, fact-check, compaction divider, files changed, approval, `ask_user`, error, stopped. | Opening it shows every card. It is the "before" screenshot set. |
| **Screenshot baseline** | Fixed set: empty state, fixture chat, long run, settings, each panel. At 1280×800 and 800×450, dark. | Saved under `docs/polish-v2/before/`. |
| **Perf baseline** | Frame timing while replaying a streaming answer, node count of the fixture chat, bundle size per chunk. | Numbers written down to compare against. |

---

## 3. Phase 1: design-system foundation

**Rebrand: Power Harness → Zen Harness** (decided 2026-10-09). Done at the start of Phase 1 so every later
screenshot carries the new identity.

| Item | Detail | Done when |
|---|---|---|
| **Logo: ensō** | A Zen brush circle: one stroke, open gap at the top right, tapered start and end, slightly uneven width, dry-brush edge. Built as an SVG path (filled outline of a variable-width stroke) plus an optional `feTurbulence` texture for large sizes only. Ink is near-white on dark; the brand accent appears only as a faint tint at the stroke's start. It must read at 16 px (favicon: a simpler, bolder stroke). | `LogoMark` in `Logo.tsx`, `public/favicon.svg` and the window icons (`web-agent/assets/zen_harness.png` / `.ico`, rendered from the SVG) all match. |
| **Name everywhere** | Wordmark "ZEN HARNESS" (`label-tech` style). Change the page `<title>` (`index.html`), the Qt window title and icon path (`web-agent/app_web.py`), strings in `SettingsDialog.tsx`, and comments in `index.css`. Keep old asset files until the new ones are confirmed. | `grep -ri "power harness"` finds nothing in the UI or the backend window code. |
| **Palette check** | Zen suggests calm. Keep the dark ink base. Propose 2–3 accent options for the user: keep cyan→violet, a single muted jade or ochre, or ink-only with one accent for state. Pick one before the tokens are finalised. | The user has chosen; tokens use it. |


| Item | Detail | Done when |
|---|---|---|
| **Elevation scale** | `--surface-0` (app) → `--surface-1` (panels, cards) → `--surface-2` (raised: popovers, active card) → `--surface-3` (modal), each with a matching border and a soft shadow for 2–3. Replace ad-hoc `bg-card/40`. | Cards, panels and popovers are distinguishable at a glance in screenshots. |
| **Type scale** | Fix to 6 sizes (11 · 12 · 13 · 14 · 15 · 18/22 display), weights 400/500/600, `label-tech` for meta. | Grep shows no stray `text-[13.5px]`-style sizes left. |
| **Spacing and radius** | 4-pt spacing steps. Radius: chips 6, cards 10, panels 12, modals 16. | Consistent in the fixture screenshots. |
| **Motion tokens** | `--dur-fast 120ms`, `--dur 200ms`, `--dur-slow 320ms`. Easing `--ease-out cubic-bezier(.2,.8,.2,1)`; spring for layout. Utility classes `enter-rise`, `enter-fade`, `collapse`. | Every animation in later phases uses tokens. |
| **State tones** | One `tone` prop (info, success, warning, destructive, muted) for pills, card edges, icons and progress. | `StatusPill`, `CardShell` and meters all use it. |
| **Focus and selection** | Brand focus ring (`ring-brand`) on all interactive elements; consistent hover (`surface-2`) and pressed states. | Tab through the app: focus is always visible. |
| **Density setting** | Comfortable / compact (spacing and font step), stored in UI settings. | Toggle changes the transcript and sidebar density live. |

---

## 4. Phase 2: app shell and top bar

| Item | Detail | Done when |
|---|---|---|
| **Top bar zones** | Left: sidebar toggle and chat title (editable inline). Centre: run status ("Working · step 14 · 3:12", or idle). Right: system pill and panel toggles. | No wrapping or clipping at 800 px. |
| **System pill** | The GPU/VRAM/tok/s/elapsed meters collapse into one pill showing what matters now: tok/s while running, VRAM when idle. A popover shows all meters plus 60-second sparklines and the model, context and cache profile. | One pill replaces four meters; the popover shows the history. |
| **Context gauge v2** | A thin bar with a compaction marker (keep). Hover shows used / left / compaction point and "N archived entries". A pulse when compaction happens. | Compaction is visible as an event, not just a number. |
| **Panel toggles** | A segmented icon group (Checklist · Browser · Artifacts · Tasks) with live badges: progress `3/7`, a red dot for console errors, artifact count, running tasks. "Reset layout" moves into the system popover. | Fewer buttons; the badges carry the information. |
| **Responsive rules** | Under 1000 px the side dock auto-collapses to tabs; under 760 px the sidebar becomes an overlay. | Screenshots at 800 px are clean. |

---

## 5. Phase 3: transcript and cards (the biggest win)

### 5.1 The run as one object

| Item | Detail | Done when |
|---|---|---|
| **Run block** | Each agent turn renders as: user message → **Run header** (status, elapsed, steps, tool calls, tokens, model) → **step timeline** → **final answer**. | The fixture long run reads as one unit. |
| **Step timeline** | Steps become compact one-line rows: icon · verb · target · outcome pill (e.g. `read_file  index.html  413 lines ✓`, `powershell  npm install  exit 1 ✗`). Click to expand into the full card. Consecutive identical steps fold into "×3". | A 28-call run fits in about one screen when collapsed. |
| **Auto-fold** | While running, the last 3 steps stay expanded. When the run ends, all steps fold and the answer is shown. Failed steps stay one click away (red pill). | Old tool cards stop dominating the chat. |
| **Live now-line** | A single animated line at the bottom of a running run: "Reading index.html…", driven by the latest `tool_call`, plus a subtle progress shimmer. | You can see what it is doing without reading cards. |
| **Final answer card** | The answer gets the emphasis: `surface-1`, a slightly larger type step, and an action row (copy · speak · retry · continue). "Stopped" and "not verified" states get proper banners. | The answer stands out in screenshots. |
| **Jump controls** | A "↓ latest" pill while scrolled up during a run, and "↑ run start" inside long runs. | Present and smooth-scrolling. |
| **Virtualisation** | Messages outside the viewport render as measured placeholders (above about 150 items). | The node budget holds on the longest real chat. |

### 5.2 Card polish

| Item | Detail |
|---|---|
| **Lifecycle animation** | Pending (shimmer edge) → result (height-animates open, tone edge fades in). Failed results shake once (or hold still under reduced motion). |
| **Background search card** | Its own design: "Searched Wikipedia + web" with favicon/domain chips for each hit, the matching Wikipedia article title, and "nothing found" as a muted state. |
| **Web hits / citations** | Favicons, domain, date. Inline `[1]` citations become hover cards (title, domain, snippet); clicking opens the source in the browser panel. |
| **Terminal** | ANSI colour, collapsible long output with "show all 312 lines", an exit-code pill, copy command, duration, and a cwd breadcrumb. |
| **Diff** | Split/unified toggle, hunk folding, a file-type icon, and "open file" into the panel. |
| **Code blocks (markdown)** | Header with language and filename, copy, wrap toggle; line numbers past 15 lines. |
| **Images** | Thumbnail grid with a lightbox (zoom, pan) for screenshots and saved frames. |
| **Approval bar** | A raised warning-toned bar pinned above the composer while pending: shows exactly what will run. Keys **Y** approve and **N** deny; it pulses once on arrival. |
| **Errors** | One error card style: what failed, the key line, and "show details". |

---

## 6. Phase 4: interactive agent moments

| Item | Detail | Done when |
|---|---|---|
| **`ask_user` form** | The questions render as a form: each question with its default as a pre-selected chip, alternative chips when the agent offers them, a free-text field, and buttons **Use defaults** / **Send answers**. Sends a structured, readable reply. *(Backend: also emit the questions as a structured event alongside the text.)* | Answering takes one click for "defaults". |
| **Interpretation card** | Collapsed by default to one line ("Understood as: …"), expandable. A **Correct this** action prefills the composer. | No longer a tall block on every turn. |
| **Plan and checklist cards** | Show `design` as a short callout and each milestone's **done when** line. The progress ring animates; items tick with a check-draw animation. | `design` and `done_when` are visible in chat and panel. |
| **Compaction divider** | "Earlier steps compressed · N entries archived" with **Browse archive**: a drawer listing archive entries (search uses the same keyword search as `history_search`). *(Backend: a small read endpoint for archive entries.)* | Archived history is reachable from the UI. |
| **Fact-check / not-verified** | The unverified claims list becomes a compact amber banner under the answer, each claim with "search it" (prefills a follow-up). | n/a |
| **Turn stats** | Becomes the run header's hover detail instead of a grey line under the answer. | n/a |

---

## 7. Phase 5: side panels, sidebar, composer, command palette

### Panels
- **Checklist panel:** a goal header with a progress ring, a design callout, and milestones as cards (title, done-when,
  subtasks, status). A notes timeline at the bottom. Current item highlighted and auto-scrolled.
- **Browser panel:** URL bar with back/forward/reload state, a page-load progress line, a **console** drawer showing
  the captured errors with file:line (badge on the tab when there are errors), and screenshot history thumbnails.
- **Artifacts panel:** version scrubber (v1…vN) with a diff between versions, a type icon, and copy/download/open
  externally.
- **Tasks panel:** each task as a card with a live progress bar, elapsed time, tail of output, and stop/approve
  actions.
- **Dockview chrome:** custom tab styling matching the elevation scale, a drag ghost, empty states with an icon, a
  one-line explanation and a call to action.

### Sidebar
- Sections: **Pinned** · **Running** · date buckets. Pin/unpin, inline rename (double-click), delete with undo toast.
- Running chats: a brand-coloured live dot and step counter. Unread dot when a run finished while you were elsewhere.
- Hover preview (first question and the last answer line). Search highlights matches and searches message text too.
- Keyboard: ↑/↓ moves through chats, Enter opens, Ctrl+N new chat. A collapse-to-icon-rail mode.
- Duplicate titles: show a disambiguating suffix (time, or the first differing word).

### Composer
- Auto-grow with a max height and a smooth height transition. Drag-and-drop overlay for files with thumbnails/chips.
- The **send button morphs** to stop while running (keeps Esc). Messages queued during a run appear as chips above
  the input ("will be read at the next step") and can be removed.
- Mode switch (Agent · Chat · Speech) as a segmented control with one-line descriptions in a tooltip; plan budget
  and thinking level grouped into one "Run settings" popover.
- Slash menu: icons, descriptions, recent items first.
- A context hint while typing: "≈ 1.2k tokens · 64% of context left".

### Command palette (`cmdk` is already installed)
- **Ctrl+K:** switch chat, new chat, toggle panels, switch model or tuned profile, open settings sections, toggle
  density/theme, run slash commands. **?** opens a shortcuts sheet.

### Settings dialog
- Left section nav with search. **Model profiles** as cards: model, context, KV cache, drafter, `n_cpu_moe`,
  estimated VRAM; edit in place with validation. Changes that will restart the model server are labelled as such.

---

## 8. Phase 6: motion pass, accessibility, performance hardening

| Item | Detail | Done when |
|---|---|---|
| **Motion library decision** | Either CSS + View Transitions only, or `motion` (motion/react, ~18 KB gzip) for layout animations (card expand, step folding, panel slide). Recommendation: `motion`, scoped to transcript and panels, lazy-loaded. | Decision recorded; within the bundle budget. |
| **Motion inventory** | Message enter (fade + 6 px rise), card expand/collapse (layout), number tickers for meters and counts, progress transitions, panel open/close slide, toast stack, check-draw. All via tokens, all off under reduced motion. | A checklist of every animation passes review. |
| **Accessibility** | Visible focus everywhere, aria-labels on icon buttons, `aria-live` for run status and the now-line, contrast ≥ 4.5:1 for text (muted-foreground on surface-1 is the risk), hit targets ≥ 28 px. | Automated contrast check and a keyboard-only walkthrough pass. |
| **Performance** | Memoised message items, virtualised transcript, code-split CodeMirror/mathlive/shiki/katex, no `backdrop-filter` stacks, animations on transform/opacity only. | Perf numbers ≥ baseline; streaming replay without dropped frames. |
| **Embedded window stability** | Test the built UI in the Qt window during a 30-minute replay loop. | No crash; memory flat. |

---

## 9. Order, size and checkpoints

| Phase | Content | Size (focused sessions) | Checkpoint for you |
|---|---|---|---|
| 0 | Replay harness, fixture, baselines | 1 | Watch a replayed run in the browser |
| 1 | Design tokens, elevation, type, motion tokens, density | 1–2 | Before/after fixture screenshots |
| 2 | Top bar and shell | 1 | Screenshots at 1280 and 800 px |
| 3 | Run block, timeline, auto-fold, card polish | 3–4 | Replay the 22-step build: readable at a glance |
| 4 | `ask_user` form, plan/checklist/compaction UI | 2 | Answer questions with one click |
| 5 | Panels, sidebar, composer, palette, settings | 3–4 | Daily-use review |
| 6 | Motion pass, a11y, perf, stability | 1–2 | Perf and a11y numbers vs baseline |

Each phase ships on its own (build → `web_dist`), with before/after screenshots in `docs/polish-v2/`. Any
backend additions (structured `ask_user` event, archive-read endpoint) are small and listed with the phase that
needs them.

## 10. Risks

- **Embedded Qt WebEngine** is the stability risk: keep effects GPU-cheap and test in the real window each phase.
- **Scope creep:** Phase 3 is the core. If time is short, ship 0 → 1 → 3 → 4 first.
- **Backend coupling:** the transcript is driven by the event stream; the run block must cope with missing or
  out-of-order events (stopped runs, reconnects).
- **Live testing conflicts:** no UI work needs the model server; replay covers it.

## 11. Decisions (2026-10-09)

1. **Theme:** dark only. The light-theme row in Phase 1 is dropped; tokens stay structured so light could be added later.
2. **Motion:** subtle (Linear-style). Short durations, small distances, no bounce; expressive effects only for the
   live "working" indicators.
3. **Density:** comfortable by default, with a compact toggle.
4. **Long runs:** fold the steps when a run ends; failed steps stay flagged and one click away.
5. **Docking:** keep dockview's free-form docking (it already works, and layouts persist), but restyle its chrome
   and ship a better default layout: chat left, one tabbed side dock (Checklist · Browser · Artifacts · Tasks).
6. **Priority:** none beyond the plan order (0 → 6).
