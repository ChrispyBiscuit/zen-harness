# Zen Harness UI vs Emil Kowalski's skills

Evaluation of `ui/src` against [emilkowalski/skills](https://github.com/emilkowalski/skills) (all 16 skills and
their reference files, read 2026-10-15). Evaluation only: no UI code was changed.

> **2026-10-15 update:** the user decided to keep the sidebar slide and the scramble effect, enhanced with chromatic aberration and a glitch. Findings #1 and #4 below are superseded; see `backend/plans/visual-polish-v3.md`.

**Method.** I read every `SKILL.md` and its references, swept `ui/src` for each rule (grep plus reading the
components that matter), and looked at the live app at `http://127.0.0.1:8765` read-only at a 1280×800 viewport.
I took screenshots and ran DOM measurements, and sent no chats or settings changes. Motion feel (easing, springs) was judged from
code. Nothing was recorded frame by frame, so the performance findings say "from code" or "needs a recording".

**Verdict.** The motion system is already close to Emil's bar. It has shared duration and easing tokens, uses
only transform and opacity, makes popovers origin-aware, keeps modals centered, never scales from 0, never uses
`ease-in`, makes exits faster than enters, and moves meter fills with `scaleX`. The gaps are in a few specific places:

- one animation on a keyboard shortcut;
- the `transition-all` on the shared button, with press feedback that isn't the standard scale;
- a decorative typewriter effect on every reply;
- a reduced-motion rule that removes all motion instead of toning it down;
- two layout breaks seen in the live app.

## Top 10 fixes (ranked by user-visible impact ÷ effort)

| # | Sev | Fix | Where | Effort |
|---|-----|-----|-------|--------|
| 1 | High | **Don't animate the Ctrl+B sidebar toggle.** It's keyboard-initiated and used many times a day, and today it's a 320ms spring slide plus a main-column glide. Toggle instantly (at most a 120ms fade). | `App.tsx:256`, `App.tsx:277-286`, `index.css:392-399` | S |
| 2 | High | **The expanded right tray covers the chat column.** Live at 1280px the chat group spans x=270–831 but the tray draws over x≈581–1018, which hides the right ~45% of the composer (mode switch, send) and the empty-state mark. Pin the tray when expanded so it pushes the column, or inset the chat by the tray width. *(Observed live; the auto-hide overlay is the likely cause, not yet confirmed.)* | `lib/dock.ts:76-91` (`expandTray`/`collapseTray`) | M |
| 3 | High | **Shared `Button`: replace `transition-all` and `active:translate-y-px`** with `transition-[background-color,color,border-color,transform] duration-(--dur-fast)` and `active:scale-[0.97]`. Every dialog, panel and toolbar button inherits this. | `components/ui/button.tsx:6` | S |
| 4 | Med | **Typewriter: drop the glyph-scramble "decode" tail and keep the caret.** It's decorative motion on the most frequent thing in the app (every reply), and each 16ms tick re-renders the whole `Markdown` tree (react-markdown, shiki, katex). Also lower `MAX_LAG` so text isn't held back by up to 350ms. | `Transcript.tsx:201-276`, `index.css:342-344` | S |
| 5 | Med | **Reduced motion should tone motion down, not remove it.** The global rule sets every animation and transition to 1ms, so opacity fades that aid comprehension go too. Keep opacity and colour at about 150ms and remove only the transforms. | `index.css:274-277` | S |
| 6 | Med | **Thread titles that share a prefix can't be told apart.** Live, 4 rows read "I want an expense tracker my…" and 3 read "Could you build me a playabl…". There's no `title` or tooltip and no way to see the full name. Add `title={t.title}` and consider a 2-line clamp or a short distinguishing suffix. | `Sidebar.tsx:40` | S |
| 7 | Med | **Tooltips: honour Base UI's `data-instant`** so the second and later tooltips in a toolbar open with no animation (`[data-slot=tooltip-content][data-instant]{animation:none}`). Then move the high-traffic icon buttons from native `title=` (~100 uses, OS delay and styling) to `Tooltip`. | `index.css:378-379`, e.g. `App.tsx:132,164`, `TrayBar.tsx`, `ToolCards.tsx` | S→M |
| 8 | Med | **No `transition-all` anywhere still in use.** Welcome progress animates `width` through `transition-all`, so reuse `MeterBar`, which already uses `scaleX`. Carousel thumbnails should list their properties instead of `all`. | `WelcomeDialog.tsx:86`, `ImageCarousel.tsx:92`, `ui/badge.tsx:7`, `ui/switch.tsx:18`, `ui/toggle.tsx:6`, `ui/tabs.tsx:58` | S |
| 9 | Low | **Image carousel motion is over budget and ungated.** Hover lift and image zoom take 300–700ms and animate `box-shadow`, with no `@media (hover:hover)` gate. The rail entrance (line 133) is 500ms and the viewer zoom 300ms. Bring all of it to 150–250ms and gate the hover. | `ImageCarousel.tsx:22-28,72,133` | S |
| 10 | Low | **Popups and dialogs use `@keyframes`**, which restart on rapid open/close. Move to transitions with `[data-starting-style]`/`[data-ending-style]` (Base UI) so they can be interrupted. While there, strengthen `--ease-out` to `cubic-bezier(0.23,1,0.32,1)`. | `index.css:46,352-379` | M |

Also worth doing, cheaply: delete the unused shadcn files (`ui/sidebar.tsx`, `ui/progress.tsx`, `ui/sonner.tsx`,
`ui/sheet.tsx`, which nothing imports, so their `transition-all`/`ease-linear` don't count against the app). Also remove the
dead `animate-in …` tw-animate classes on primitives whose motion `index.css` overrides (`ui/dialog.tsx:34,56`,
`ui/popover.tsx:37`, `ui/dropdown-menu.tsx:41,135`, `ui/select.tsx:85`, `ui/hover-card.tsx:40`, `ui/tooltip.tsx:52`).
Right now two motion systems appear to apply and only one does.

---

## Per skill

Severity: **High** = visible every session or breaks a task · **Med** = visible often or a clear rule violation ·
**Low** = polish or rarely seen.

### emil-design-eng (main philosophy)

Rules: decide whether to animate by frequency (100+/day → never, and never on keyboard actions). Name a purpose. Use `ease-out`
for enter/exit and strong custom curves, never `ease-in`. Keep UI under 300ms. Press feedback `scale(0.97)`, never
`scale(0)`. Popovers origin-aware, modals centered. Tooltips delay first, then instant. Transitions over keyframes
for interruptible UI. Animate only transform and opacity, never `transition: all`. Reduced motion means gentler, not
zero. Gate hover motion. Exits faster than enters. Stagger 30–80ms.

**Passes**
- Motion tokens exist and are used: `--dur-fast/--dur/--dur-slow`, `--ease-out`, `--ease-spring` (`index.css:46-47,207-210`).
  `--ease-spring` is exactly Emil's iOS drawer curve.
- Enter and exit motion is transform and opacity only (`index.css:266-271,346-399`), and the utility `transition-ui`
  lists its properties (`index.css:272`).
- Popovers, menus, selects and hover cards use `origin-(--transform-origin)` with a 4px nudge from the open side
  (`index.css:363-376`, `ui/popover.tsx:37`, etc.). Dialogs stay centered.
- Nothing scales from 0. Entrances start at 0.94–0.985 (the Clip mascot's 0.7 is a rare delight moment and allowed).
- No `ease-in` anywhere.
- Exits are faster than enters: dialog 320ms in and 140ms out, popover 190/110, tooltip 120/90.
- Tooltip provider delay is 300ms (`App.tsx:274`).
- One focus ring for keyboard focus only (`index.css:241-244`).

**Fails**

| Sev | Before | After | Why | Where |
|-----|--------|-------|-----|-------|
| High | Ctrl+B → 320ms spring slide and main glide | Instant toggle (or a ≤120ms fade) | Never animate keyboard-initiated, frequent actions | `App.tsx:256,277-286` |
| High | `transition-all … active:translate-y-px` | `transition-[background-color,color,transform]` + `active:scale-[0.97]` | Name the exact properties; pressing should scale | `ui/button.tsx:6` |
| Med | Reduced motion → every animation set to 1ms | Keep opacity and colour (~150ms), drop transforms | Reduced means gentler, not none | `index.css:274-277` |
| Med | Tooltip animates on every open | `[data-instant]{animation:none}` | Later tooltips should be instant | `index.css:378` |
| Low | Popups and dialogs use `@keyframes` | Transitions with `data-starting-style`/`data-ending-style` | Keyframes restart from zero when interrupted | `index.css:352-379` |
| Low | `--ease-out: cubic-bezier(0.2,0.8,0.2,1)` | `cubic-bezier(0.23,1,0.32,1)` | Built-in-strength curves feel weak | `index.css:46` |
| Low | Card hover `-translate-y-0.5` + 700ms image zoom, ungated | 200ms, inside `@media (hover:hover) and (pointer:fine)` | >300ms, and hover motion isn't gated | `ImageCarousel.tsx:22-28` |

### animate · review-animations · improve-animations (share `STANDARDS.md` / `AUDIT.md`)

These three apply the same standards to building, reviewing and auditing motion. Below is the audit across the eight
`AUDIT.md` categories.

| Category | Result |
|----------|--------|
| 1. Purpose & frequency | **Fail**: Ctrl+B sidebar (top-10 #1). **Fail**: typewriter scramble on every reply (#4). Shimmer "Thinking"/"Meditating" text is state indication: pass. |
| 2. Easing & duration | Mostly pass. Fail: carousel 500–700ms (`ImageCarousel.tsx:28,133`); the 320ms `--dur-slow` on the sidebar and dock group is just over budget for frequent panels (`index.css:386-387,396-398`). Modal 320ms is within the 200–500 band: pass. |
| 3. Physicality & origin | Pass on origin and scale. Partial on press feedback: raw `<button>`s use `active:bg-surface-*` colour feedback (`Sidebar.tsx:31`, `Transcript.tsx:78…`, `ToolCards.tsx:110,387`), which is fine for list rows, but the shared `Button` should scale (#3). |
| 4. Interruptibility | Low: keyframes on popups and dialogs (#10). The sidebar uses `onAnimationEnd` state (`App.tsx:281`), so a second Ctrl+B mid-slide restarts rather than reverses. Removing the animation (#1) removes the issue. |
| 5. Performance | See animation-performance below. |
| 6. Accessibility | Reduced motion is handled globally, plus a JS check in the typewriter and dock and a still InkRing (`index.css:321`). It over-removes (#5). Hover motion is only in the carousel, where it's ungated. |
| 7. Cohesion & tokens | Pass: one token set. Minor: hand-typed `140ms`, `160ms`, `110ms`, `90ms`, `190ms` exits/enters (`index.css:359,361,374,375,379`) could become `--dur-exit`/`--dur-pop` tokens. |
| 8. Missed opportunities | See find-animation-opportunities. |

### animation-performance

Rules: animate transform and opacity (filter, clip-path and background-color are acceptable). Never `transition: all`. Don't
animate through React state per frame. Avoid inherited CSS-variable-driven transforms. Keep blur under 20px. Add `will-change`
only once a problem is measured.

**Passes:** `MeterBar` moves the fill with `scaleX` and says so (`MeterBar.tsx:13-14`). Dock and sidebar motion is
transform-only. No `will-change` sprinkling. Typewriter uses timers that stop in hidden windows (`Transcript.tsx:237`).

**Fails**

| Sev | Element | Cause | Before → After | Verified |
|-----|---------|-------|----------------|----------|
| Med | Streaming reply | React re-render per frame | `setN` + `setFrame` every 16ms re-parses the whole Markdown → drop the scramble's `setFrame`, render the settled prefix memoised and only the tail live, or tick at ~30Hz | from code; needs a Performance recording during a long reply |
| Med | Welcome download bar | Layout property | `transition-all` on `width` → `MeterBar` (`scaleX`) | from code (`WelcomeDialog.tsx:86`) |
| Low | Carousel card | Paint property | `transition-[box-shadow,transform]` → `filter: drop-shadow()` or a static shadow with only the lift animating | from code (`ImageCarousel.tsx:22`) |
| Low | Composer focus | Paint property | `transition-[border-color,box-shadow]` (`Composer.tsx:208`) is occasional and small, so leave it | from code |
| Low | `.zen-main-from-*` | Variable read once per keyframe | `translateX(var(--side-w))` is static during the animation, so it's fine. Mentioned only because the skill flags variable-driven transforms. | from code |
| Low | Backdrop blur | Blur cost | `backdrop-blur-xl` on the full-screen image viewer (`ImageCarousel.tsx:66`) is 24px and above the 20px guidance. It's static rather than animated, so cost is limited; use `-lg` (16px). | from code |

### find-animation-opportunities

After the gate (frequency → purpose → speed → function), the suggestions that survive:

| # | Location | Today | Purpose | Frequency | Suggested motion |
|---|----------|-------|---------|-----------|------------------|
| 1 | `Transcript.tsx` card fold (`toggle(k)`, e.g. `:78,97,140,170`) | Card bodies teleport open and closed | State indication | Tens/day | Short opacity + 4px rise on the body (`enter-fade` already exists), 150ms `--ease-out`. No height animation. |
| 2 | `WelcomeDialog.tsx` first-run completion | Flat | Delight (rare) | Once | Allowed a longer beat: a check-draw or a 30–60ms stagger of the "ready" rows |
| 3 | Checklist item ticks (`Checklist.tsx`) | Instant | State indication | Occasional | Check-draw via `stroke-dashoffset` at 200ms, as plan Phase 4 already proposes |

**Rejected:** command palette or slash menu open/close (`SlashMenu.tsx`, keyboard, 100+/day: never animate). Status bar
number tickers for CPU/RAM (plan §8 "number tickers": functional data read at a glance, so motion hinders). Animated
message entrance for every message (plan §8; tens/day and the typewriter already marks arrival). "Failed results
shake once" (plan §5.2: not a valid purpose at this frequency, and a red tone edge already does the job).

### apple-design

Rules: respond on press. Make motion interruptible from the current value. Use springs for gestures, symmetric
enter/exit paths and anchored origins. Avoid slow looping oscillation near 0.2Hz. Respect
`prefers-reduced-transparency` and `prefers-contrast`. Use size-specific tracking. Prefer undo to confirms.

**Passes:** the sidebar enters from the left and exits to the left (symmetric). Popovers are anchored. Translucent chrome
is used sparingly with no stacked glass. `label-tech` uses positive tracking at 11px, which is right for small text
(`index.css:279`).

**Fails**

| Sev | Issue | Fix | Where |
|-----|-------|-----|-------|
| Low | Destructive and reset actions use `window.confirm()` (unstyled OS dialog). Thread delete in particular would be better as undo. | Delete immediately and show a `toast` with an **Undo** action. Keep the confirm only for irreversible actions (API token rotation). | `Sidebar.tsx:58`, `App.tsx:162`, `ModelsPanel.tsx:109`, `Schedules.tsx:102`, `ToolCards.tsx:770`, `ServicePanel.tsx:58` |
| Low | Clip mascot `clip-bob` loops at 3.6s (~0.28Hz), close to the slow-oscillation band Apple warns about | Bob only when it appears or talks, then rest | `index.css:403,409` |
| Low | No `prefers-reduced-transparency` / `prefers-contrast` handling for `backdrop-blur` surfaces | Make them solid under those queries | `ImageCarousel.tsx:63,66,73,131`; `index.css` |
| Low | Display text has no negative tracking (`--text-display` 22px, `--text-title` 18px) | `letter-spacing: -0.01em` to `-0.02em` on title and display | `index.css:36-39` |

### break-ui

Rules: test with plausible worst-case data. Use `min-width:0` and `flex-shrink:0`. Use middle truncation where items differ at the end.
Truncated text needs a way to read it. Plurals through `Intl.PluralRules`. `tabular-nums` for live numbers. Check
narrow containers.

**Passes:** thread rows have `min-w-0` + `truncate`, and their status icons have `shrink-0` (`Sidebar.tsx:38-45`).
Meters use `tabular-nums` and `whitespace-nowrap` (`App.tsx:40-43`). Plurals are handled in the visible counts ("row{s}",
"1 place" in `ToolCards.tsx:531`, `ImageCarousel.tsx:136`). Numbers use `toLocaleString()`. Carousel images fall back on
error and use `object-cover`.

**Fails**

| Sev | Field | Worst case (seen live) | What happens | Fix |
|-----|-------|------------------------|--------------|-----|
| High | Chat column vs expanded tray | 1280px window, artifacts tray open | Tray overlays the composer; its controls and the right edge are hidden | Top-10 #2 (`lib/dock.ts`) |
| Med | Thread title | 4 threads starting "I want an expense tracker my…" | Rows look identical and there's no tooltip | `title={t.title}` + 2-line clamp (`Sidebar.tsx:40`) |
| Low | Composer placeholder | Narrow chat column | "Give the agent a task… (type / for com" cut mid-word | Shorter placeholder below an `@container` width (`Composer.tsx:258`) |
| Low | SQL title in Query card | Long SQL | `.slice(0, 90)` with no ellipsis or full view | `truncate` + `title={d.sql}` (`ToolCards.tsx:530`) |
| Low | Thread "•••" menu | Touch or pen input | Only visible on hover (has `focus-visible`, so keyboard is fine) | Acceptable for a desktop app; note only (`Sidebar.tsx:50`) |

Not checked: RTL and 200% zoom (no fixture). The polish-v2 replay fixtures (`npm run replay`) would be the place for a
worst-case dataset toggle.

### ask-sonner

**Passes:** one `<Toaster />` mounted once at the root, outside dialogs (`App.tsx:297`). `theme="dark"` is set
explicitly, which matters because Sonner defaults to light and the app is dark-only. `richColors` and `closeButton` are on. `toast.error`/
`toast.success` are used from event handlers, not effects, so there are no StrictMode duplicates.

**Fails (Low):** `components/ui/sonner.tsx` is an unused shadcn wrapper whose `useTheme()` has no `ThemeProvider`, so it
would be a trap if anyone switched to it. Delete it. Toast styling isn't tied to the tokens (default Sonner palette next to the
`surface-*` system): pass `toastOptions.classNames` with `!` utilities or go headless once Phase 5 restyles toasts.
Consider `toast.promise` for "Compressing the conversation…" (`Composer.tsx:142`) so it resolves to success or error
instead of leaving a stale message.

### pick-ui-library

**Passes:** the stack matches what the skill recommends: Base UI primitives, Sonner, cmdk, and no hand-rolled toast or popover.
Polish-v2 §8 proposes `motion` scoped to the transcript, which fits. Use the full `transform` string (not `x`/`y`
shorthands) for anything that plays while a reply is streaming.

### Not applicable

- **animate-expo, mobile-native, write-swift:** React Native, mobile web and Swift. Zen Harness is a desktop Qt
  WebEngine app.
- **prototype:** a workflow for building UI variants, with no rules to check against.
- **animation-vocabulary:** a glossary for prompting. Useful when briefing the Phase 6 motion pass.

## Alignment with plans/graphical-polish-v2.md

Phases 0–1 match these skills. A few items planned for later phases conflict with them and are worth changing before
they're built:

- §8 "number tickers for meters and counts": skip for the status bar (functional data, high frequency).
- §5.2 "failed results shake once": replace with the tone edge alone.
- §7 command palette (cmdk): ship it with **no** open/close animation (Raycast rule).
- §8 "panel open/close slide": fine for mouse-driven opens. Keyboard toggles (Ctrl+B, Ctrl+Alt+B) stay instant.
- §8 motion library: if `motion` is adopted, use transform strings and springs with `bounce: 0` by default (Apple's
  damping 1.0), keeping bounce for gestures.
