# Phase 1: design tokens (reference)

All in `src/index.css`; Tailwind utilities are generated from `@theme inline`. `lib/utils.ts` teaches
tailwind-merge the new names (otherwise `cn()` drops `text-ui` as if it were a colour).

| Token | Utilities | Values |
|---|---|---|
| Elevation | `bg-surface-0..3`, `border-line-1..3`, `shadow-elev-2/3` | 0 app · 1 panels/cards · 2 raised (popovers, hover, active) · 3 modal |
| Type | `text-meta` 11 · `text-caption` 12 · `text-ui` 13 · `text-body` 14 · `text-chat` 15 · `text-title` 18 · `text-display` 22 | density-aware via `--fs-*` |
| Radius | `rounded-chip` 6 · `rounded-card` 10 · `rounded-panel` 12 · `rounded-modal` 16 | dockview groups use `--radius-panel` |
| Rhythm | `px-(--card-px) py-(--card-py)`, `min-h-(--row-h)`, `gap-(--gap-item)` | 4-pt; compact shrinks them |
| Motion | `--dur-fast` 120 · `--dur` 200 · `--dur-slow` 320 ms, `ease-out`, `ease-spring`; `enter-rise`, `enter-fade`, `collapse`, `transition-ui` | transform/opacity only; reduced motion = off |
| Tone | `data-tone="info|success|warning|destructive|muted"` + `tone-text`, `tone-soft` (13 %), `tone-wash` (6 %), `tone-fill`, `tone-edge` | `lib/tone.ts` (`Tone`, `levelTone`) |
| Focus | global `:focus-visible` outline in `--focus-ring`; shadcn rings use `--ring` (= ink) | keyboard only |
| Density | `html[data-density=compact] [data-density-scope]` | `lib/density.ts`; Settings → Appearance; `settings.ui.density` + localStorage |
| Brand | `--ink`, `--brand-a/-b` (ink-only palette) | the ensō tint and focus hook |

Components on the system: `StatusPill`, `CardShell`, `Row` (ToolCards), `MeterBar` (top-bar meters, context gauge,
checklist progress), `StepIcon`, sidebar rows, transcript cards, dialogs/popovers/selects/menus.

Not converted yet (left for the phases that rework them): half-step spacings (`px-2.5`, `py-1.5`) outside cards,
the diff add/del row colours (syntax, not state), `text-info` links, and `shadow-[…]` one-offs in the composer.
