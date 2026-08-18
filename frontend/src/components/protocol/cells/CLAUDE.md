# Cells view UI details

Scoped detail for the Cells tab of the protocol canvas's `ExperimentSidePanel` — this only
loads when you're working in this directory. These rules came from the (now-deleted) static
`ExperimentDetailPage`; they survived the move into the canvas unchanged in substance, only
re-sized for a ~384px panel column. See the root `CLAUDE.md`'s "Tables vs. cards is a
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
  unreadably wide). `table-fixed` isn't used since the column set is dynamic; instead:
  `truncate` on free-text cells, a shaded uppercase sortable header row, subtle zebra striping,
  `font-mono` on technical values. It's styled to the side panel's own compact table idiom (a
  plain `text-xs` `<table>` in a bordered box, like `RunsTab`/`ResultsTab`), not
  `components/ui/table`'s page-width rows. The caller (`CellsTab`) owns the one
  `overflow-x-auto` wrapper — the column set can outgrow even the maximized overlay.
- **A factorial design gets a heatmap complement above its table, not instead of it**
  (`CellsHeatmap.tsx`) — precise numbers still matter, a heatmap just makes the *shape* of a
  multi-factor sweep's results scannable at a glance. 1-2 factors render as one grid; 3 facet
  into one grid per level of the third. Replicate cells sharing one factor combination are
  averaged, not just the first one picked. Color is `color-mix(in oklch, var(--muted),
  var(--primary) N%)` (`heatColor` in `lib/experiment.ts`) — low values dim, high values glow
  in the theme's own accent, not an unrelated rainbow scale. The `compact` prop is only a
  layout switch for the panel (stacked header, smaller squares); same data, same rules.
  - **Both axes (factors) and color (metric) are derived from the cells' own data, with
    zero setup required** (`deriveFactors`/`availableMetricKeys`/`pickDefaultMetric` in
    `lib/experiment.ts`) — NOT from `design_spec.factors`/`task_brief.selection_metric`.
    Those two fields are only ever optional hints for a nicer default (an explicit
    `design_spec.factors` wins when present so levels can be deliberately ordered;
    `task_brief.selection_metric` only picks which observed metric key is pre-selected). Do
    not make either a hard requirement again: `task_brief` is a notebook-local variable
    embedded straight into agent prompts, not something the backend record has — nearly every
    real experiment has both fields `null`, and requiring them would mean the heatmap never
    renders for anyone who didn't know to separately attach this metadata. Factors are derived
    from the union of every cell's `factor_values` keys, excluding a small bookkeeping
    blocklist (`replicate`/`seed`/`rep`/`trial`/`iteration`) so a replicate index doesn't
    become a spurious extra axis. The metric selector only shows when there's more than one
    numeric key to choose from. Silently renders nothing for >3 derived factors, <2 cells, or
    no numeric metric anywhere on the cells at all — those are exactly the cases where it
    can't show anything the table doesn't already say better.
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
- Paginate client-side once a list can realistically exceed ~10-20 rows.
