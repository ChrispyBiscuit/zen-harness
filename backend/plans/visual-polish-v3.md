# Visual polish v3: the signal look (amends graphical-polish-v2, phases 2–6)

**Goal:** finish the Zen Harness look and feel by combining four inputs:

- the remaining polish-v2 phases;
- the Emil Kowalski skills audit (`ui/docs/skills-eval.md`);
- a survey of the most-starred design-skill repos (§7);
- the user's direction from 2026-10-15.

**User direction (2026-10-15), binding:**
1. **Keep the sidebar slide** (Ctrl+B and the button). It stays animated. The work is to make it smoother, not shorter.
2. **Keep the scrambled-letters decode effect.**
3. **Enhance it with chromatic aberration and a glitch effect.**

Where a skill rule conflicts with these (Emil's "never animate keyboard actions" and "no decorative motion on frequent
elements", impeccable's "dark-glow" and "techy costume" warnings), the user's direction wins. As impeccable itself
puts it, a pinned brief beats the generic warnings. The skills still set the *how*: transform and opacity first, short,
bounded, interruptible, with a real reduced-motion path.

**Character:** the polish-v2 "quiet, precise instrument" with one deliberate exception. **Text arriving from the
model looks like a signal resolving.** Ink-monochrome everywhere else, with colour meaning state, and the glitch is the
single place the UI breaks its own calm. Restraint everywhere else is what makes the glitch read as intentional
rather than noisy.

---

## 1. Principles and budgets (on top of v2 §1)

1. **One signature motif: decode/glitch.** It appears where information is *arriving or changing state*: the
   streaming reply, and optionally a few rare moments (§3.4). It never appears on chrome, navigation or idle UI.
2. **Colour = state, plus the fringe.** Chromatic-aberration fringes (red/cyan) are the only non-state colour in
   the app, and they exist only while text is resolving. Settled text is always plain ink.
3. **Motion budget:** UI transitions 120–250ms. Signature moments may run longer: the sidebar 320ms, glitch bursts
   80–180ms each. Exits are faster than entrances. Every animation is interruptible, or short enough not to matter.
4. **Reduced motion means gentler, not none.** Keep opacity and colour changes, drop movement, jitter and cycling
   glyphs. This replaces the global 1ms kill switch (`index.css:274-277`), which impeccable, Emil and Vercel all flag.
5. **Performance gate (unchanged from v2):** the streaming replay at 4× must stay at ≥59.5 fps with p99 ≤17ms and no
   new frames over 33ms (baseline 59.9 fps / 16.9ms, `ui/docs/polish-v2/baseline.md`). The glitch must be CSS-only
   and paint only small regions.
6. **Bundle:** this whole plan adds under 3KB gzip (CSS plus a few lines of TS), within v2's +60KB.

---

## 2. Phase A: fix what's broken (do first; folds into v2 Phase 2)

These are defects visible in the live app or in the v2 "after" screenshots. No taste calls are involved.

| # | Fix | Where | Effort |
|---|-----|-------|--------|
| A1 | **The expanded right tray overlays the chat column.** In the live app at 1280px it covers the right ~45% of the composer, hiding the mode switch and send button. Pin the tray when expanded so it takes layout space, or inset the chat column by the tray width. | `lib/dock.ts:76-91` | M |
| A2 | **The send button overflows the composer.** It sits half outside the composer's right border in `after/empty-1280x800.png` and `after/fixture-mid1-1280x800.png`. Constrain the composer footer row (`min-w-0` on the middle controls, `shrink-0` on send), and below a container width collapse "adaptive" and "Plan 2k" into icons. | `Composer.tsx` footer row | S |
| A3 | **Clipped labels.** The chat title shows as "F…" and the panel tab as "Artifac…". Give the top-bar title `min-w-0 flex-1` before the model chip, and give dock tabs `title` attributes plus a min width, or icon-only tabs when narrow. | `App.tsx` TopBar, `dock-theme.css` | S |
| A4 | **Threads that share a prefix look identical** (live: 4 rows read "I want an expense tracker my…"). Add `title={t.title}` and a 2-line clamp for rows whose first 28 characters collide. | `Sidebar.tsx:40` | S |
| A5 | **Shared `Button`.** Replace `transition-all` with the listed properties, and replace `active:translate-y-px` with `active:scale-[0.97]` (transform 120ms). | `ui/button.tsx:6` | S |
| A6 | **Remove the remaining `transition-all`.** The welcome download bar should use `MeterBar` (scaleX), and the carousel thumbnails should list their properties. | `WelcomeDialog.tsx:86`, `ImageCarousel.tsx:92`, `ui/badge·switch·toggle·tabs` | S |
| A7 | **Screen readers read the scramble.** Add `aria-hidden="true"` and `user-select:none` to `.scramble`, so assistive tech and copy-paste see only real text. | `Markdown.tsx:62` | S |
| A8 | **Delete dead shadcn files** (`ui/sidebar.tsx`, `ui/progress.tsx`, `ui/sonner.tsx`, `ui/sheet.tsx`, none imported) and remove the stale `animate-in …` tw-animate classes that `index.css` overrides. Today there appear to be two motion systems, but only one applies. | `components/ui/*` | S |

**Done when:** the v2 screenshot set (`scripts/shoot.mjs`) at 1280×800 and 800×450 shows no clipped or overlapping
controls, with the tray open and with it closed.

---

## 3. Phase B: the signal motif (folds into v2 Phase 3, transcript)

### 3.1 Anatomy of a resolving reply

Today's decode effect (`Transcript.tsx:201-276`, `Markdown.tsx:53-73`, `index.css:331-344`): settled text, then a
cursor, then up to 8 cycling glyphs in info-blue with a blue glow. The redesign:

| Layer | What | Runs |
|-------|------|------|
| **1. Settled text** | Plain ink. No effect, ever. Readable and selectable. | always |
| **2. Decode tail** (existing scramble) | The 8 cycling glyphs. Colour changes from info-blue to ink at 85%, with static **chromatic fringes** instead of the blue glow: `text-shadow: -1px 0 var(--ca-r), 1px 0 var(--ca-c)`. | while typing |
| **3. Glitch bursts** (new) | About every 0.9–2.6s (random) while typing, the tail and the last settled ~24 characters do a **burst**: two pseudo-layers (`::before` red, `::after` cyan, `content: attr(data-text)`) slice with `clip-path: inset()` and jitter ±2px horizontally on `steps(2)` for 120–180ms, then disappear. A burst never moves layout or the real text. | while typing |
| **4. Cursor** | The block caret keeps blinking. Its blue glow becomes a red/cyan split `box-shadow: -1px 0 var(--ca-r), 1px 0 var(--ca-c)`. During a burst it skews by `skewX(-8deg)` for one step. | while typing |
| **5. Lock-in** | When the reply finishes, one final 160ms burst on the last line, then everything settles to ink. This replaces the abrupt stop. | once per reply |

**Tokens** (new, in `:root` in `index.css`):
```css
--ca-r: oklch(0.68 0.2 25 / 0.55);   /* red fringe   */
--ca-c: oklch(0.8 0.13 210 / 0.55);  /* cyan fringe  */
--ca-x: 1px;                          /* split offset; 2px at "full" intensity */
--glitch-dur: 150ms;
```

**Mechanics**
- **Slices:** the rehype plugin already wraps the tail in `<span class="scramble">`. It also sets
  `data-text={inside}` so the pseudo-layers can mirror the glyphs.
- **Glitch window:** a second wrapper, `.glitch-zone`, covers the last ~24 settled characters, made with the same
  SCR_* sentinel approach (new `GLT_OPEN`/`GLT_CLOSE`).
- **Bursts:** the typewriter loop already ticks on a timer. It toggles one class (`is-glitching`) on the message
  container **through a ref (`el.classList`)**, not through React state, so a burst costs no re-render. The class is
  removed after `--glitch-dur`.
- **Keyframes:**
  ```css
  @keyframes zen-glitch-slice {
    0%   { clip-path: inset(12% 0 58% 0); translate: calc(-2 * var(--ca-x)) 0 }
    50%  { clip-path: inset(56% 0 14% 0); translate: calc(2 * var(--ca-x)) 0 }
    100% { clip-path: inset(0 0 100% 0);  translate: 0 0 }
  }
  .is-glitching :is(.scramble, .glitch-zone)::before,
  .is-glitching :is(.scramble, .glitch-zone)::after {
    content: attr(data-text); position: absolute; inset: 0; pointer-events: none;
    animation: zen-glitch-slice var(--glitch-dur) steps(2, end) 1 both; }
  .is-glitching :is(.scramble, .glitch-zone)::before { color: var(--ca-r); }
  .is-glitching :is(.scramble, .glitch-zone)::after  { color: var(--ca-c); animation-delay: 30ms; }
  ```
  `translate` is compositor-only. `clip-path` and `text-shadow` paint, but only over a few dozen glyphs, which is
  within ibelick's and Emil's "paint only small, isolated regions" rule.
- **Stops cleanly:** when the window is hidden the typewriter already flushes, so no bursts are scheduled. When the
  reply ends, every class is removed and settled text carries no residue.

**Intensity setting** (Settings → Appearance → "Text effects"), stored as `ui.text_fx`:

| Setting | Decode tail | Fringes | Bursts | Lock-in |
|---------|-------------|---------|--------|---------|
| **Full** | 8 glyphs | `--ca-x: 2px` | every 0.6–1.8s, ±3px | yes |
| **Signal** (default) | 8 glyphs | 1px | every 0.9–2.6s, ±2px | yes |
| **Calm** | 8 glyphs | 1px static | none | no |
| **Off** | none (plain caret) | none | none | no |

**Reduced motion** (`prefers-reduced-motion: reduce`, overrides the setting):
- The decode tail shows *static* glyphs that don't cycle.
- Fringes stay at 0.5px and static.
- No bursts, no lock-in, no caret skew.
- Text still types in. That is content arriving, not decoration, though at a faster minimum rate (120 chars/s).

**Accessibility:**
- The tail and pseudo-layers are `aria-hidden` (A7).
- Settled text keeps ≥4.5:1 contrast at all times. During a burst the real glyphs stay underneath at full opacity; the layers only add colour on top.
- No flashing: a burst is under 3 per second and only a small region, well inside WCAG 2.3.1.

### 3.2 Where else the motif may appear (rare moments only; each optional, decide in §6)

| Moment | Effect | Frequency |
|--------|--------|-----------|
| "Thinking" → "Reasoning" and "Planning the approach" → "Approach" label swaps (`Transcript.tsx:60,172`) | The new word decodes in (6 glyphs, 220ms) instead of swapping instantly; the existing shimmer is kept while live | a few per reply |
| A failed tool card | One 160ms burst on the card title, **replacing v2 §5.2's "shake once"**, so failure speaks the same visual language | rare |
| Empty state headline "What should we work on?" | Decodes on first mount per session (400ms) | once per session |
| Model switch in the top-bar chip | The new model name decodes in | rare |

Not on: sidebar rows, buttons, menus, tooltips, panel tabs, numbers in meters, code blocks or tool output (mono text
the user reads exactly).

### 3.3 Streaming performance hygiene (needed so the glitch stays smooth)

- **Memoise the settled prefix:** split the reply at its last completed block, render everything before it once with
  `React.memo`, and re-render only the trailing block plus the tail on each tick. Today the whole `Markdown` tree is
  re-parsed every 16ms (react-markdown, shiki, katex), which is fine on the laptop (59.9 fps) but is the first thing
  to fail on a slower GPU.
- **Burst toggles via the ref, never state** (above).
- **Verify with the replay perf run** (§1.5) at Signal and Full, before and after.

---

## 4. Phase C: the sidebar slide, kept and made smoother (folds into v2 Phase 2)

The slide stays at its current character: a 320ms spring-curve slide, with the main column gliding.

| # | Change | Why |
|---|--------|-----|
| C1 | **Make it interruptible.** Today it is driven by `@keyframes` plus `onAnimationEnd` state (`App.tsx:277-286`), so pressing Ctrl+B twice quickly restarts it from the edge. Move it to a CSS `transition` on `transform` (with `@starting-style` for the first open), so a reversal mid-slide turns around from where it is. | Emil, Apple and Vercel all list interruptibility as the core rule for anything users can re-trigger |
| C2 | **Exit faster than enter:** open 320ms `--ease-spring`, close 220ms `--ease-out`. It is already roughly so; make it explicit in tokens. | Asymmetric timing |
| C3 | **Keep the sidebar contents still** during the slide: no per-row stagger, and keep the opacity floor at 0.4 as now. | Avoids motion that competes with the slide |
| C4 | **Reduced motion:** a 150ms crossfade, no translate. | §1.4 |
| C5 | **Same treatment for the right tray** (Ctrl+Alt+B): it grows from the right edge with the same curve, so both edges behave alike (Apple: symmetric paths, anchored origins). | Cohesion |

---

## 5. Phase D: visual system refinements (folds into v2 Phases 3, 5 and 6)

These are rules the surveyed skills agree on, checked against the current UI.

### 5.1 Motion system

- **One ease-out curve:**
  - `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` replaces the weaker `(0.2,0.8,0.2,1)` (`index.css:46`).
  - The other skills give near-identical curves: Emil `(0.23,1,0.32,1)`, impeccable `(0.16,1,0.3,1)`, transitions.dev and taste `(0.22,1,0.36,1)`.
  - Keep `--ease-spring` (Emil's drawer curve).
- **Name the stray durations:** `--dur-exit: 140ms` and `--dur-pop: 190ms` replace the hand-typed 90/110/140/160/190ms (`index.css:359-379`).
- **Popovers, menus and dialogs** move from `@keyframes` to transitions using `[data-starting-style]`/`[data-ending-style]` (Base UI), so they can be interrupted. Tooltips honour `[data-instant]` and skip the animation when sliding along a toolbar.
- **Rewrite reduced motion** as in §1.4.
- **Image carousel:** gate hover motion behind `(hover:hover) and (pointer:fine)`, and bring the 300–700ms durations to ≤250ms (`ImageCarousel.tsx:22-28,72,133`).

### 5.2 Typography

- **Type scale ratio.** It is 11/12/13/14/15/18/22, about 1.08 per step at the small end. impeccable's
  "flat hierarchy" check wants at least one ≥1.25 jump: make `--text-display` 24px (from 22) and keep 18 for titles.
- **Tracking by size:**
  - `letter-spacing: -0.01em` at 18px and `-0.02em` at 22–24px (Apple, taste, impeccable).
  - Body stays at 0.
  - `label-tech` keeps +0.12em, which is correct for 11px caps.
- **`text-wrap: balance`** on headings in `.prose-chat` and on dialog titles. **`text-wrap: pretty`** on chat paragraphs.
- **`slashed-zero`** in the `mono` utility (`font-feature-settings: "zero"`), for code and paths (userinterface-wiki).
- **Ellipsis and quotes:** use `…`, not `...`, across UI strings, and placeholders end in `…` (Vercel).

### 5.3 Colour and depth

- **Glow budget:** the only glows left in the app should be the CA fringes. Remove the blue `box-shadow` glow from the
  caret (`index.css:337`) and the `text-shadow` glow from `.scramble` (`index.css:344`). Both are replaced in §3.1.
  impeccable flags coloured glow on dark; the fringe is the deliberate exception.
- **Info-blue means "working" only.** Today it also colours file names, links, the Plan chip and the context meter
  (see the fixture screenshots). Move links and paths to ink with an underline or `--ink` mono, and keep blue for
  running and info states.
- **Muted tiers contrast:**
  - Audit `text-muted-foreground/60` (meter labels, `App.tsx:41`) and the other alpha'd greys with a contrast script.
  - Targets are 4.5:1 for text and 3:1 for icons and focus (impeccable, ui-ux-pro-max, Vercel).
  - Alpha-on-alpha greys are the likeliest failures.
- **Card depth:**
  - Cards use a hairline (`line-1`) and no resting shadow, which passes.
  - Floating surfaces (popover, dialog) use the layered `--elev-2/3` with an inset top highlight, which passes.
  - Keep that rule explicit: shadows only on things that float.
  - Concentric radius: inner radius = outer − padding (`--radius-panel` 12 with 2px inset → 10 = `--radius-card`, already consistent; check the composer and chips).
- **Browser chrome:**
  - `<meta name="theme-color">` = `--surface-0`.
  - `overscroll-behavior: contain` on side panels and dialogs.
  - `color-scheme: dark` is already set (passes).

### 5.4 Interaction

- **Native `title=` tooltips → `Tooltip`** on the high-traffic icon buttons: top bar, tray bar, card actions (~100 `title=` uses today).
- **`window.confirm()` → undo toasts** for thread delete and schedule delete (`Sidebar.tsx:58`, `Schedules.tsx:102`).
  Keep a real dialog (not `confirm`) only for irreversible actions: API token rotation, "undo every file change".

---

## 6. Decisions for the user

Each line gives a recommendation first.

1. **`label-tech` (mono uppercase, tracked) on the empty-state card eyebrows ("SYSTEM", "DOCUMENTS") and the
   "LOCAL · GROUNDED · VERIFIED" pill.** Three surveyed skills (frontend-design, impeccable, taste) flag this as
   generic AI styling. With the glitch identity it reads as HUD voice. **Recommend:** keep it for meters, status and
   section headers (functional); drop it from card eyebrows; keep the pill.
2. **Empty-state grid background** (`index.css:282`). impeccable flags decorative grids, but it fits the
   signal/HUD look. **Recommend:** keep it, and let it flicker once with the headline decode (§3.2).
3. **Optional scanline/noise texture.** ui-ux-pro-max's chromatic and cyberpunk styles add scanlines at 0.05–0.1
   opacity. **Recommend:** no permanent texture. At most, a 0.04 scanline that exists only inside a glitch burst.
4. **Status-bar number tickers** (v2 §8). These are functional numbers read at a glance, so Emil and impeccable say
   no. **Recommend:** no tickers on CPU/RAM/GPU; yes on rare counters (checklist "3/13").
5. **Command palette (v2 §7, cmdk).** It is keyboard-opened many times a day. **Recommend:** a 100ms opacity fade
   only, no scale or slide. Say if you want more.
6. **Default text-effect intensity:** **Signal** (recommended) or **Full**.

---

## 7. Sources (surveyed 2026-10-15, by GitHub stars)

| Repo | ★ | What it contributed |
|------|---|---------------------|
| [anthropics/skills](https://github.com/anthropics/skills), frontend-design | 181k | Anti-slop list (decorative mono labels, middle-dot strings, uniform radius and shadow), token-plan then critique process |
| [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | 136k | Contrast floors, a motion cap of 1–2 elements per view, exit at 60–70% of enter; styles #67 Chromatic Aberration (`-2px/2px` RGB split, 0.3s glitch), #41 Cyberpunk, #73 Terminal; pre-delivery checklist |
| [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill) | 97k | Screenshot → critique → fix-top-3 loop (≤3 rounds), and "if a problem is visible in a screenshot, it is real". Its house rules (no mono, hairlines or glows) target marketing pages and are not adopted. |
| [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | 82k | Operate-mode motion 150–250ms, exponential ease-out, reduced motion that keeps opacity, flat-hierarchy check, dark-glow rule, 11px functional-text floor, audit scoring (5 dimensions × 0–4, P0–P3) |
| [emilkowalski/skills](https://github.com/emilkowalski/skills) | 46k | Full audit in `ui/docs/skills-eval.md`: easing and durations, interruptibility, origin-aware popovers, press scale |
| [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) → web-interface-guidelines | 32k | Checklist: `aria-live` on async updates, `min-w-0` and truncation, `tabular-nums`, `theme-color`, `overscroll-behavior`, ellipsis character, undo before destroy |
| [ibelick/ui-skills](https://github.com/ibelick/ui-skills) | 10k | Blur ≤8px and one-shot, paint only small isolated regions, no CSS-variable-driven transforms, pause loops off-screen |
| [Jakubantalik/transitions.dev](https://github.com/Jakubantalik/transitions.dev) | 5k | Motion token scale (80/150/250/350/400ms), `data-text` pseudo-element overlay pattern (shimmer-text), streaming-text blur 1px / 60ms per word |

No surveyed repo ships a glitch or scramble recipe. §3.1 is composed from their primitives (ui-ux-pro-max's RGB-split
values, transitions.dev's `attr(data-text)` overlay, ibelick's paint limits) plus the existing decode code.

---

## 8. Order and checkpoints

| Step | Content | Depends on | Checkpoint |
|------|---------|------------|------------|
| 1 | Phase A (fixes) | — | Screenshot set shows no clipping or overlap, tray open and closed |
| 2 | §5.1 motion tokens + reduced-motion rewrite | — | Grep shows no `transition-all`; reduced-motion screenshots |
| 3 | Phase B §3.1 decode + glitch, §3.3 perf | 2 | Replay perf at Signal and Full ≥59.5 fps, p99 ≤17ms; a screen recording of a resolving reply for the user to approve the feel |
| 4 | Phase C sidebar | 2 | Ctrl+B spam test: reverses smoothly, never restarts |
| 5 | §5.2–5.4 type, colour, interaction | 1 | Contrast script passes; before/after screenshots |
| 6 | §3.2 rare moments (only those approved in §6) | 3 | Recording |
| 7 | Visual gate: screenshot loop (taste-skill, ≤3 rounds), Emil audit re-run, perf re-run | all | `ui/docs/polish-v3/` before/after + numbers |

All work runs against the replay (`npm run replay`, `perf:build`/`perf:serve`), never the live model or GPU (v2 §1.6).
The live app in `Desktop\Diffusion\web-agent` is updated only by the normal build-and-sync step, and GitHub is kept in sync.
