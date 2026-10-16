# Colour and depth: proposal

**Ask (2026-10-15):** the UI "looks a little flat and dimensionless, not really much interesting colour… some
restraint without just being totally boring."

**How this was made:** I researched the top design repos (listed at the end). I then mocked up four directions as CSS
injected into the *real* app, running the replay fixtures, with no app code changed. Each direction's CSS is in this
folder (`depth.css` plus `v*.css`), and the screenshots sit next to it. `compare-fixture.png` and `compare-run.png`
show all four side by side with today's UI.

## Why it reads flat today

1. **Every surface is the same distance from black.**
   - The app background, panels and cards span L 0.145–0.25, with chroma ≈ 0.005. That is neutral grey with no
     temperature.
   - Panels sit on a ground of the *same* colour, so nothing floats.
2. **There is no light source.**
   - No surface has a lit top edge, and panels have no shadow. Only the composer has one.
   - Separation comes entirely from 7% white hairlines.
3. **There is no accent at all.**
   - The primary button is white, and active, selected and focus states are grey.
   - The only colour on screen is state (a green progress bar, a red "1 failed"). The eye has nowhere to land.

## The research, condensed

The sources agree on these rules (sources at the end):

- **One accent, under ~5% of any screen,** used only for: the primary action, where you are (active row and panel),
  focus and selection, and one "alive" signal. Never on headings, icons or bullets. (taste-skill, impeccable `colorize`)
- **Tint the neutrals toward the accent's hue** (chroma 0.006–0.016), so the greys have a temperature. Never use pure
  `#000` or `#fff`. (impeccable `quieter`, taste, Radix)
- **Depth on dark comes from lightness steps and edge light, not glow.**
  - Use a darker ground with panels 4–5 L-points above it, and cards a step above those.
  - Every raised surface gets an inner top highlight, `inset 0 1px 0 white/6%`, the single highest-value fix
    (coss/Cal.com, magicui).
  - Shadows are a contact layer plus an ambient layer (tweakcn).
- **At most one static radial wash** of the accent at 6–10%, where the eye starts. Add ~3% film grain on the fixed
  shell only, to kill banding. No animated gradients, neon, aurora or glass. (magicui, minimalist-skill, impeccable)

## The shared depth layer (in every direction)

These changes go in whichever hue is chosen (`depth.css`):

| Change | Value |
|---|---|
| Ground (window, sidebar, top bar, status bar) | L 0.13, slightly tinted |
| Panels (dock groups) float on it | L 0.17, lit top edge, contact + ambient shadow |
| Cards, user bubble, composer: a step up | L 0.205–0.238, lit top edge |
| Accent wash at the top of the chat panel | radial, 4–9% of the accent |
| Film grain over the shell | 3.5%, overlay blend, fixed (never on scrolling content) |
| Accent roles | send button, focus ring and composer rim, selection, active chat row, active tray icon and marker, link hover |

## Four directions

| | Neutrals | Accent | Moves a state colour | Character |
|---|---|---|---|---|
| **A · Depth only** | cool graphite (h265) | the warm ink itself | — | Today's identity with dimension. Still monochrome. |
| **B · Kin, gold leaf** ★ | warm graphite (h70) | `oklch(0.80 0.115 85)` | warning → orange (h50) | The gold sits on the ink's own hue (85), so it reads as the brand rather than an addition. Calm, premium, warm. |
| **C · Ai-zome, indigo ink** | navy-tinted (h272, C 0.016) | iris `oklch(0.70 0.13 278)` | info → cyan (h220) | Cool, "night desk", the most visibly coloured. Near the generic "AI purple" look; kept desaturated to stay clear of it. |
| **D · Shu, vermilion seal** | neutral sumi (h40) | vermilion `oklch(0.66 0.165 37)` | destructive → rose (h2) | The most "zen" (a hanko stamp). Bold, but it sits closest to the error colour, even after moving destructive. |

**Recommendation: B, Kin (gold leaf) on the shared depth layer.**

- It answers "not boring" with real colour, but the accent comes from the ink you already have.
- It costs the fewest state-colour changes: only warning moves.
- It pairs well with the red/cyan glitch, whose fringes stay the only cool colour on screen.

**Offer the others as a theme setting.** All four are a dozen CSS variables, so Settings → App → Accent (Ink / Kin / Ai
/ Shu) costs almost nothing, and you can live with each before deciding.

## Two depth extras worth adding (not in the mockups)

- **Spotlight on hover for transcript cards** (magicui `magic-card`): a 180px radial of the accent at 12–14% follows
  the pointer, set by one rAF-throttled pointer handler. Cheap, and it repaints only the hovered card.
- **A running beam** (magicui `border-beam`, pure CSS `offset-path`): a short accent highlight travels the border of
  the *one* card that is running right now, and stops when it's done. It reuses the "alive" role and pairs with the
  glitch. One at a time, paused when the window is hidden, off under reduced motion.

## To ship the chosen direction

1. **Tokens.** Move `--ground`, the tinted neutral ramp, `--acc`/`--acc-on`, `--hi` (edge light) and `--lift` (panel
   shadow) into `index.css`, with the theme as a `data-accent` attribute. It mirrors `data-text-fx`, stored as
   `settings.ui.accent`.
2. **Real hooks, not selectors.** The mockups target classes (`aside .group.relative.bg-surface-2`). The real version
   puts `data-active` on the active chat row and tray icon, plus a `.surface-raised` utility for cards and the
   composer.
3. **Checks.**
   - The contrast script on the new ramp (text ≥ 4.5:1 and controls ≥ 3:1, including accent-on-accent for the send button).
   - The screenshot set.
   - The streaming perf run: grain and wash are static, so expect no cost.
4. **Order:** about half a day for tokens + B + the theme setting, then the spotlight, then the beam.

## Sources

- [pbakaus/impeccable](https://github.com/pbakaus/impeccable): `colorize`, `quieter`, `bolder`, `craft-floor`
- [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill): `references/colour.md`, minimalist, soft and redesign skills
- [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill): `data/colors.csv`, `styles.csv`
- [magicuidesign/magicui](https://github.com/magicuidesign/magicui): border-beam, shine-border, magic-card, dot-pattern
- [jnsahaj/tweakcn](https://github.com/jnsahaj/tweakcn): dark presets, layered shadow map
- [radix-ui/colors](https://github.com/radix-ui/colors): the 12-step dark scales and what each step is for
- [cosscom/ui](https://github.com/cosscom/ui) (Cal.com): white-mix elevation and inner top highlights
