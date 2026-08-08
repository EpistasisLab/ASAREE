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
  - **Agent grids aren't a table either** (`ExperimentDetailPage.tsx`'s `AgentsSection`): a
    dynamic card grid — icon, name, model badge, goal, run count, last-used — not a plain
    table of name/role/added like ARES's Agents tab (that thin-table pattern was explicitly
    what the user wanted to move away from).

# Experiment data model

- **`ResearchExperiment.dataset_id` is a real, nullable FK** to `registered_datasets`
  (migration `a1b2c3d4e5f6`), unlike agents below — a dataset genuinely is a first-class,
  one-to-one property of an experiment worth a real relationship, matching what ARES does.
  It's set via `PATCH /experiments/{id}`, not at creation: the notebook's Step 1 (create the
  experiment) runs *before* Step 2 (register the dataset), so there's nothing to attach yet
  at create time — see the `client.experiments.update(...)` call added right after Step 2 in
  `spinal_pipeline.ipynb`. Experiments created before this migration simply have
  `dataset_id: null` — that's an expected, permanent state for them, not a bug to backfill.
- **Agents are deliberately NOT a stored relationship** — there's no `experiment_agents` join
  table, and none is planned. Agents are reusable per-user templates, not something an
  experiment "owns"; asking "which agents ran in this experiment" is answered by scanning the
  user's `Run`s for `run_metadata.experiment_id` matches (see `AgentsSection` in
  `ExperimentDetailPage.tsx`) and cross-referencing `GET /agents`, not by a new backend
  association. `GET /runs` has no server-side `experiment_id` filter (only `agent_id`) — this
  fetches every run for the user and filters client-side, which is fine at today's scale but
  is the place to add a real filter if a user's run history grows large enough to matter.
