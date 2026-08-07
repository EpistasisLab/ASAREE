# Frontend visual style

The frontend (`frontend/`) uses a deliberate dark, retrofuturist/high-tech visual language.
Preserve it for all new pages and components — don't revert to shadcn's plain default theme
or introduce a different aesthetic without being asked.

- **Theme**: dark mode only (`<html class="dark">` in `index.html`), a graphite background
  (not near-black) with a cyan/electric-blue accent (`--primary`). Tokens live in
  `frontend/src/index.css`'s `.dark` block — adjust values there, don't hardcode colors in
  components.
- **Motifs**: a faint cyan grid backdrop and scanline texture on `body`, glowing buttons
  (`components/ui/button.tsx` — every variant glows in its own accent color: `default`/
  `outline`/`secondary`/`ghost` in `--primary`, `destructive` in `--destructive`; only `link`
  has no surface to glow), and glowing HUD-style corner brackets on cards
  (`components/ui/card.tsx`'s `Card`, plus `components/AuthLayout.tsx`). These live in the
  shared primitives, not per-page overrides — a new page gets them for free by using
  `Card`/`Button`/`AppHeader`, so build on those rather than hand-rolling styles.
- **Monospace for technical readouts**: IDs, hashes, token prefixes, cell labels, and
  key=value data dumps use `font-mono` (see `ExperimentDetailPage.tsx`, `ApiTokensSection.tsx`)
  to read like a real data/terminal output, not prose.
- **Every button glows uniformly, by explicit choice**: an earlier pass reserved the glow for
  one "primary" action per section (`default` variant only), with `outline`/`ghost` left
  plain — the user found that inconsistent and asked for uniform glowing buttons instead.
  Don't reintroduce the primary-only hierarchy; if a future change to `button.tsx` needs a
  visual hierarchy again, ask first rather than assuming the old convention.
- **Tables vs. cards is a zoom-level decision, not a style preference**: a "container" list —
  sparse metadata, click one to drill into it (e.g. the Experiments list) — is a card/tile
  grid, not a table; a table only earns its place once you're inside that container looking
  at dense, precise, multi-attribute records to compare (e.g. an experiment's Cells, with
  their metrics/hyperparameters). Don't reach for a table just because a list exists — check
  which zoom level you're actually building first.
  - **Tile grids** (`ExperimentsPage.tsx`): `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3
    gap-4` of `Card`s, each with a truncated title + `line-clamp-3` description (`title=`
    attribute carries the full text on hover, since the card stays compact rather than
    growing to fit long text — the full description belongs on the detail page one click
    in) + a small metadata badge/date — not a dense data dump. Client-side sort via a `Select` + direction
    toggle (column-header sort doesn't exist once there are no columns). Don't add a KPI
    summary strip above the grid unless there's a real aggregate to show (e.g. cells
    scored) — a stat card restating the list's own item count, or one that just repeats a
    field already visible on every tile (design type, most-recent item), isn't a summary,
    it's noise. Per-tile polish that *is* worth it: a design-type icon next to its badge, a
    thin glowing top accent strip, relative time (`"2h ago"`, exact date on hover via
    `title`), a short `font-mono` id fragment, a very low-opacity decorative watermark icon,
    and a hover lift (`scale-[1.02]` + intensified glow + the trailing arrow shifting to
    `text-primary`).
  - **Data tables** (`ExperimentDetailPage.tsx`'s Cells table): `table-fixed` with explicit
    `w-[…%]` per header so columns stay proportional regardless of content length, `truncate`
    on free-text cells, a shaded uppercase header row, subtle zebra striping, `font-mono` on
    technical values.
  - Both: paginate client-side once a list can realistically exceed ~10-20 rows.
