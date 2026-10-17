# Dervo design: where things live

Read this before styling anything in `frontend/src/local`. The bar is the Claude Code desktop app: smooth hovers, nothing jumps, no visual mistakes. It should still feel like Dervo.

## Files

- `DervoTokens.css`: every colour, space, radius, type size, shadow and duration, as `--dv-*` variables scoped to `:root[data-dervo-local]`, with light, dark and forest themes. Add a token here before you hardcode a value anywhere else.
- `DervoComponents.css`: shared component rules (sidebar items, rows, the hover and selected states).
- `DervoBrand.css`: buttons (`data-dv-btn` variants and `data-dv-size`), inputs, focus rings and the window surface.
- `DervoKit.css`: floating material (menus, popovers, dialogs, sheets, toasts, tooltips).
- `DervoControls.tsx`: React controls (Switch, Segmented, ChoiceRows, Select, TextField, Tag, SettingRow, SettingsSection). Use these instead of building new ones.
- `StatusGlyph.tsx`: the one status mark. Use it for any agent or task state.

## Rules

- Hover: use `--dv-hover` for the background, `--dv-press` while pressed and `--dv-selected` plus `--dv-selected-edge` for the current item. Fade in over `--dv-hover-in` (90ms) and out over `--dv-hover-out` (180ms). Only the background changes.
- Never animate width, height, padding or margin. Use opacity, transform, background or box-shadow, so nothing around the element moves.
- Things that swap places on hover (the time and the ⋯ menu on a row) share one slot and crossfade with opacity. Don't use `display: none`.
- One pine primary button per view. Rust (`--dv-rust`) only marks the first thing that needs the person. Show each status once per screen.
- Serif (Instrument Serif) is only for the Home greeting and onboarding. Everything else is Instrument Sans, with tabular numerals for times and counts, and JetBrains Mono for code and paths.
- No photo wash behind the sidebar. The meadow image is for brand moments (`--dv-meadow`).
- Every interactive element needs a visible `:focus-visible` state. The global ring is in `DervoBrand.css`.
- Respect `prefers-reduced-motion`: keep colour changes, drop movement.

## Checking your work

Tests don't prove design quality. Render the screen in light and dark at 1052 and 1512 wide and look at it, including hover, focus, empty and error states.
