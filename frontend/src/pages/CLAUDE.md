# Experiments-list page UI details

Scoped detail for `ExperimentsPage.tsx` — this only loads when you're working in this
directory. See the root `CLAUDE.md`'s "Tables vs. cards is a zoom-level decision, not a style
preference" principle for the general rule this detail implements: a "container" list — sparse
metadata, click one to drill into it (e.g. the Experiments list) — is a card/tile grid, not a
table; a table only earns its place once you're inside that container looking at dense,
precise, multi-attribute records to compare (e.g. an experiment's Cells, with their
metrics/hyperparameters).

- **Tile grids** (`ExperimentsPage.tsx`): `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3
  gap-4` of `Card`s, each with a truncated title + `line-clamp-3` description (`title=`
  attribute carries the full text on hover, since the card stays compact rather than
  growing to fit long text — the full description belongs one click in) + a small metadata
  badge/date — not a dense data dump. Client-side sort via a `Select` + direction
  toggle (column-header sort doesn't exist once there are no columns). Don't add a KPI
  summary strip above the grid unless there's a real aggregate to show (e.g. cells
  scored) — a stat card restating the list's own item count, or one that just repeats a
  field already visible on every tile (design type, most-recent item), isn't a summary,
  it's noise. Per-tile polish that *is* worth it: a design-type icon next to its badge, a
  thin glowing top accent strip, relative time (`"2h ago"`, exact date on hover via
  `title`), a short `font-mono` id fragment, a very low-opacity decorative watermark icon,
  and a hover lift (`scale-[1.02]` + intensified glow + the trailing arrow shifting to
  the card's accent color).
- **A tile click goes to the protocol canvas** (`/experiments/{id}/protocol`), not to a
  static detail page — there isn't one, and `/experiments/{id}` exists only as a redirect
  (see `App.tsx`). Everything about a single experiment lives in the canvas's side-panel
  tabs; the rules for the Cells heatmap/table specifically are in
  `components/protocol/cells/CLAUDE.md`.
- Paginate client-side once a list can realistically exceed ~10-20 tiles.
