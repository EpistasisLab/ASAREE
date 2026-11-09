# Frontend instructions

Applies throughout `frontend/`. Read the root instructions first and applicable nested
instructions before editing. Paths below are relative to `frontend/src/` unless stated.

## Visual language

- Preserve the dark, retrofuturist theme: graphite background, cyan/electric-blue primary,
  faint cyan body grid and scanlines, and glowing HUD card brackets. Keep dark mode on
  `frontend/index.html`; adjust color tokens in `index.css`'s `.dark` block.
- Build with shared `Card`, `Button`, `AppHeader`, and `AuthLayout` primitives so new views
  inherit the theme. Change shared motifs in those primitives rather than per-page overrides.
- Every surfaced button variant glows uniformly: `default`, `outline`, `secondary`, and
  `ghost` use `--primary`; `destructive` uses `--destructive`. `link` has no surface glow.
  Ask before introducing a primary-only glow hierarchy or changing the aesthetic.
- Use `font-mono` for IDs, hashes, token prefixes, cell labels, and key=value readouts.
- Use card/tile grids for sparse container lists that drill into a detail view. Use tables
  for dense records with multiple precise attributes to compare inside a container.

## Meaningful color

- Tint by status or category, never by array index or rotation for decoration.
- Set a card accent with `style={cardAccent('var(--chart-3)')}` from `lib/utils.ts`.
  Matching icons, badges, and hover arrows use
  `text-[color:var(--card-accent,var(--primary))]` so they follow the card's tint.
- Use `cellsStatusAccent` in `lib/experiment.ts` everywhere cell progress is shown:
  amber (`--chart-4`) = generated/unscored; cyan (`--primary`) = partly scored;
  emerald (`--chart-3`) = fully scored; muted foreground = no cells.
- Use `hashToChartHue` from `lib/utils.ts` for open-ended categories, such as agent model
  names. Equal model names must share a tint. Canvas node kinds use the explicit mapping
  described in [protocol instructions](src/components/protocol/AGENTS.md).

## Where to read next

- Before editing page behavior, read [page instructions](src/pages/AGENTS.md).
- Before editing canvas, inspector, panel, or experiment navigation behavior, read
  [protocol instructions](src/components/protocol/AGENTS.md), including when the caller
  lives in `pages/` or a helper lives in `lib/`.
- Before editing legacy cell tables/heatmaps or their aggregation helpers, read
  [Cells instructions](src/components/protocol/cells/AGENTS.md).

## Verification

From `frontend/`, use `npx tsc -b` for types, `npx oxlint <changed-paths>` for lint,
and `npx vitest run <test-file>` for affected behavior. Use installed dependencies;
report missing prerequisites. Follow root approval rules for expensive checks and browser use.
