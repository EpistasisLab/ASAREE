# Experiments/Cells page UI details

Scoped detail for `ExperimentsPage.tsx` and `ExperimentDetailPage.tsx` — this only loads when
you're working in this directory. See the root `CLAUDE.md`'s "Tables vs. cards is a zoom-level
decision, not a style preference" principle for the general rule this detail implements: a
"container" list — sparse metadata, click one to drill into it (e.g. the Experiments list) — is
a card/tile grid, not a table; a table only earns its place once you're inside that container
looking at dense, precise, multi-attribute records to compare (e.g. an experiment's Cells, with
their metrics/hyperparameters).

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
  the card's accent color, see below).
- **A real table has real columns, not two key=value string dumps** (`ExperimentDetailPage.tsx`'s
  `CellsTable`): a `Factors`/`Metrics` column rendering `"tier=cloud-standard, effort=medium"`
  as flat text is not "a table," it's two text blobs wearing a table's styling — you still have
  to visually parse every row to compare anything. `CellsTable` gives one real, independently
  sortable column per derived factor (reuses `deriveFactors`) plus up to 4 curated metric
  columns (`pickMetricColumns` — same preference order as the heatmap's default metric, capped
  because a real scored cell can have 6-7 numeric keys and showing all of them makes the table
  unreadably wide). `table-fixed` isn't used here since the column set is dynamic; instead:
  `truncate` on free-text cells, a shaded uppercase sortable header row, subtle zebra striping,
  `font-mono` on technical values.
- **A factorial design gets a heatmap complement above its table, not instead of it**
  (`ExperimentDetailPage.tsx`'s `CellsHeatmap`) — precise numbers still matter, a heatmap
  just makes the *shape* of a multi-factor sweep's results scannable at a glance. 1-2
  factors render as one grid; 3 facet into one grid per level of the third. Replicate
  cells sharing one factor combination are averaged, not just the first one picked.
  Color is `color-mix(in oklch, var(--muted), var(--primary) N%)` — low values dim, high
  values glow in the theme's own accent, not an unrelated rainbow scale.
  - **Both axes (factors) and color (metric) are derived from the cells' own data, with
    zero setup required** (`deriveFactors`/`availableMetricKeys`/`pickDefaultMetric`) —
    NOT from `design_spec.factors`/`task_brief.selection_metric`. Those two fields are
    only ever optional hints for a nicer default (an explicit `design_spec.factors`
    wins when present so levels can be deliberately ordered; `task_brief.selection_metric`
    only picks which observed metric key is pre-selected). Do not make either a hard
    requirement again: `task_brief` is a notebook-local variable embedded straight into
    agent prompts, not something the backend record has — nearly every real experiment
    has both fields `null`, and requiring them would mean the heatmap never renders for
    anyone who didn't know to separately attach this metadata. Factors are derived from
    the union of every cell's `factor_values` keys, excluding a small bookkeeping
    blocklist (`replicate`/`seed`/`rep`/`trial`/`iteration`) so a replicate index doesn't
    become a spurious extra axis. The metric selector only shows when there's more than
    one numeric key to choose from. Silently renders nothing for >3 derived factors, <2
    cells, or no numeric metric anywhere on the cells at all — those are exactly the
    cases where it can't show anything the table doesn't already say better.
- **Cells/factor_values/metric_values are a `design_type === 'factorial'` concept, not a
  universal one** (`ExperimentDetailPage.tsx`) — the "Cells"/"Best metric" stat cards and the
  whole Cells card (heatmap + table) are gated behind that check. `design_type` is a plain
  string specifically so another experiment type could exist later (ab_experiments,
  discoveries, etc. are explicitly out of scope on `ResearchExperiment` itself, not something
  the frontend invented) — a non-factorial experiment gets an explicit "Cell-based results
  aren't available for '{type}' experiments" card instead of an empty/broken cells view. There
  is no other experiment type implemented anywhere in ASAREE today, so this can't be exercised
  with real data yet — don't take that as a reason to remove the guard; it's cheap insurance
  for a boundary ASAREE's own backend already declared.
- **A wide dynamic table earns a "maximize" toggle** (`CellsSection`'s `fullscreen` state) — a
  fixed `inset-0 z-50` overlay with an Escape-key handler and a close button, NOT the browser
  Fullscreen API (`requestFullscreen()` hides browser chrome entirely, a bigger commitment than
  "let me read this table" calls for). Lock `document.body` scroll while open and restore it on
  close/unmount. Reach for this pattern again for any other view whose content can outgrow the
  page's `max-w-5xl` column — don't reintroduce the Fullscreen API for the same job.
- Both: paginate client-side once a list can realistically exceed ~10-20 rows.
- **Agent grids aren't a table either** (`ExperimentDetailPage.tsx`'s `AgentsSection`): a
  dynamic card grid — icon, name, model badge, goal, run count, last-used — not a plain
  table of name/role/added like ARES's Agents tab (that thin-table pattern was explicitly
  what the user wanted to move away from).
