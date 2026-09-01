# Cells view UI details

Scoped detail for the Cells tab of the protocol canvas's `ExperimentSidePanel` — this only
loads when you're working in this directory. These rules came from the (now-deleted) static
`ExperimentDetailPage`; they survived the move into the canvas unchanged in substance, only
re-sized for the panel column (384px by default, drag-resizable from 320px up). See the root
`CLAUDE.md`'s "Tables vs. cards is a
zoom-level decision" principle for the general rule this implements: you're *inside* one
experiment here, comparing dense multi-attribute records, which is exactly where a real table
earns its place.

- **A real table has real columns, not two key=value string dumps** (`CellsTable.tsx`): a
  `Factors`/`Metrics` column rendering `"tier=cloud-standard, effort=medium"` as flat text is
  not "a table," it's two text blobs wearing a table's styling — you still have to visually
  parse every row to compare anything. `CellsTable` gives one real, independently sortable
  column per derived factor (reuses `deriveFactors`) plus up to 4 curated metric columns
  (`pickMetricColumns` — same preference order as the heatmap's default metric, capped because
  a real scored cell can have 6-7 numeric keys and showing all of them makes the table
  unreadably wide). One table row is one true cell (a unique factor combination): its metric
  columns are replicate means and its status reports scored/total replicates. The API's legacy
  `Replicate` rows are individual observations and must be grouped first via
  `groupReplicatesIntoCells`. `table-fixed` isn't used since the column set is dynamic; instead:
  `truncate` on free-text cells, a shaded uppercase sortable header row, subtle zebra striping,
  `font-mono` on technical values. It's styled to the side panel's own compact table idiom (a
  plain `text-xs` `<table>` in a bordered box, like `RunsTab`/`ResultsTab`), not
  `components/ui/table`'s page-width rows. The caller (`CellsTab`) owns the one
  `overflow-x-auto` wrapper — the column set can outgrow even the maximized overlay.
- **A factorial design gets a heatmap complement above its table, not instead of it**
  (`CellsHeatmap.tsx`) — precise numbers still matter, a heatmap just makes the *shape* of a
  multi-factor sweep's results scannable at a glance. 1-2 factors render as one grid; 3 facet
  into one grid per level of the third. Replicates within each cell are averaged, not just the
  first one picked. Color is `color-mix(in oklch, var(--muted),
  var(--primary) N%)` (`heatColor` in `lib/experiment.ts`) — low values dim, high values glow
  in the theme's own accent, not an unrelated rainbow scale. Its layout is driven by
  **container queries** (`@lg`/`@2xl`, with `CellsTab`'s `CellsBody` marking the `@container`),
  not by the viewport and not by a compact/roomy prop — it renders both inside the
  drag-resizable panel and in the full-viewport overlay, so "how much room is there" is a
  question about the box, and dragging the panel wider has to pay off without a width threaded
  through three components. Keep it that way if you add more responsive behaviour here.
  - **Both axes (factors) and color (metric) are derived from the cells' own data, with
    zero setup required** (`deriveFactors`/`availableMetricKeys`/`pickDefaultMetric` in
    `lib/experiment.ts`) — NOT from `design_spec.factors`/`task_brief.selection_metric`.
    Those two fields are only ever optional hints for a nicer default (`design_spec.factors`
    supplies deliberate level *ordering*, and levels that are planned but have no cell yet;
    `task_brief.selection_metric` only picks which observed metric key is pre-selected). Do
    not make either a hard requirement again: `task_brief` is a notebook-local variable
    embedded straight into agent prompts, not something the backend record has — nearly every
    real experiment has both fields `null`, and requiring them would mean the heatmap never
    renders for anyone who didn't know to separately attach this metadata. Factors are derived
    from the union of every cell's `factor_values` keys, excluding a small bookkeeping
    blocklist (`replicate`/`seed`/`rep`/`trial`/`iteration`) so a replicate index doesn't
    become a spurious extra axis.
  - **Which factors exist is decided by the cells, never by `design_spec.factors`** — this
    is the bug that made the heatmap "disappear" once already, so don't undo it.
    `deriveFactors` used to return a declared spec verbatim whenever one existed. Real data
    here has both conventions: the *Myocardial Infarction* experiment's cells are keyed by
    the declared display names (`Azure Foundry:Model`, `Critic enabled`), while the *Spinal
    Fusion* one declares those same-style names but its cells are keyed `tier`/`effort`/
    `critic` from the design generator. Nothing keeps the two in sync. When they diverged,
    every `replicatesMatching()` lookup matched 0 of 80 replicates, every square came out `null`, and
    the grid vanished with no error in the console, no failing type, and no visible clue —
    it looked exactly like a broken component. A declared spec now only contributes level
    order and planned-but-unrun levels for names the cells actually use. The metric selector only shows when there's more than one
    numeric key to choose from. For >3 derived factors, <2 cells, or no numeric metric
    anywhere on the cells at all it draws no grid — those are exactly the cases where it
    can't show anything the table doesn't already say better — but it says so in a one-line
    `HeatmapUnavailable` note rather than rendering `null`. That's deliberate and worth
    keeping: it used to bail silently, and a heatmap that can disappear for five different
    data-shaped reasons without naming one is indistinguishable from a heatmap that's broken.
    Route any new bail-out through that component instead of returning `null`.
  - **The grid has to survive a 320px column** — it was written for a ~1024px page and moved
    into the panel, where `grid-template-columns: auto repeat(N, 1fr)` overflowed: a grid
    won't shrink an `auto` track below its content, so one long factor level name (a model
    id, a dict-ish value) sized the label track past the panel's whole width and pushed every
    square out through the `Card`'s `overflow-hidden` edge — the heatmap read as *missing*,
    not as squeezed. It's now `fit-content(7rem) repeat(N, minmax(2.5rem, 1fr))` inside an
    `overflow-x-auto` box, with `overflow-hidden` + `truncate` on the label cells (that pair
    is what drops their min-content contribution to zero and lets the 7rem cap actually
    bind). Don't reintroduce a content-sized track or drop the per-square minimum.
- **Cells/factor_values/metric_values are a `design_type === 'factorial'` concept, not a
  universal one** — `CellsTab` and the canvas top bar's own cells/best-metric chips are both
  gated behind that check. `design_type` is a plain string specifically so another experiment
  type could exist later (ab_experiments, discoveries, etc. are explicitly out of scope on
  `ResearchExperiment` itself, not something the frontend invented) — a non-factorial
  experiment gets an explicit "Cell-based results aren't available for '{type}' experiments"
  line instead of an empty/broken cells view. There is no other experiment type implemented
  anywhere in ASAREE today, so this can't be exercised with real data yet — don't take that as
  a reason to remove the guard; it's cheap insurance for a boundary ASAREE's own backend
  already declared.
- **A dense view that outgrows the panel earns a "maximize" toggle** (`CellsTab`'s `fullscreen`
  state, same as `ResultsTab`'s) — a fixed `inset-0 z-50` overlay with an Escape-key handler
  and a close button, NOT the browser Fullscreen API (`requestFullscreen()` hides browser
  chrome entirely, a bigger commitment than "let me read this table" calls for). Lock
  `document.body` scroll while open and restore it on close/unmount. The table is mounted in
  exactly one of the two places at a time, never duplicated with its own divergent sort/page
  state. Reach for this pattern again for any other panel view that can't fit — don't
  reintroduce the Fullscreen API for the same job.
- **The panel-vs-tab split**: Cells is the raw "what did each configuration score" grid;
  `ResultsTab` is the statistical analysis computed *on* those numbers (effects, CIs,
  non-inferiority). Keep them separate — don't fold cells into Results.
- **Agent lists aren't a table either** — but they're no longer a card grid either. The old
  detail page's `AgentsSection` card grid became `ExperimentAgents` in `RunsTab.tsx`: a
  compact one-row-per-agent list (icon tinted by model hash, name, model badge, run count,
  relative last-used) since it's a footnote to the trial table in a narrow column, not its own
  page section. The model-hash tint is the part that carries meaning and must survive any
  further restyling; don't go back to ARES's plain name/role/added thin table either.
- **Design history is a collapsed footnote, not a peer of the table** (`DesignHistory.tsx`):
  regenerating a design that would drop cells supersedes the whole revision rather than editing
  or deleting the old cells (see the root `CLAUDE.md`'s "Cells belong to a design revision"),
  so an experiment quietly accumulates results the current view doesn't show. The list renders
  **only once there are ≥2 revisions** — a permanent "1 revision" row is noise in a 320px
  column, and a single current revision is just "the design", which is already the whole rest
  of the tab. Selecting a superseded revision swaps the heatmap/table onto its cells with an
  explicit read-only banner; that selection lives in `CellsTab` so the tally, the CSV button and
  the grid can't disagree about which design they're showing, and a superseded revision gets its
  own query key so it never overwrites the `['experiments', id, 'cells']` entry the canvas top
  bar and `DesignTab` share. Deleting one is permanent and cascades to its results, so it goes
  through a `Dialog` that names the counts (the `DeleteNodeConfirmDialog` convention), not the
  lighter inline two-click confirm — and the current revision has no delete affordance at all,
  since the server refuses it (409).
- Paginate client-side once a list can realistically exceed ~10-20 rows.
