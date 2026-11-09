# Legacy Cells display instructions

Read [protocol instructions](../AGENTS.md) and the shared
[experiment contracts](../../../../../docs/agents/experiment-contracts.md) before changing
cell displays, aggregation helpers, or design-history behavior. These rules also apply to
callers outside this directory. Current Runs/Results use `ExecutionBrowser`; apply these
rules when maintaining/reusing these components, not as a requirement to restore a Cells tab.

## Table and aggregation

- One row is one cell (unique factor combination); metric columns show replicate means
  and status shows scored/total replicates. Give each derived factor its own sortable
  column; reuse `deriveFactors` and `pickMetricColumns` for the curated column set.
  Keep factors/metrics independently comparable rather than packing them into string dumps.
- Read `GET /experiments/{id}/cell-summaries`, which returns SQL-aggregated sum/count data.
  Do not fetch every replicate and its artifacts merely to calculate display averages.
  Use `pooledMean` so pooled cells remain replicate-weighted.
- Use the compact panel table idiom: `text-xs`, bordered box, shaded uppercase sortable
  headers, subtle zebra stripes, truncated free text, and monospace technical values.
  Columns are dynamic; avoid `table-fixed` and page-width primitive rows. The caller owns
  one horizontal-scroll wrapper, including when content is maximized.
- Paginate once lists can realistically exceed roughly 10–20 rows.

## Heatmap

- Complement the precise table with a heatmap: one grid for one/two factors and facets
  for three. Average replicates instead of selecting the first. Use theme-based
  `heatColor` from `lib/experiment.ts`, not an unrelated rainbow palette.
- Derive factor names from the union of cells' `factor_values` keys, excluding bookkeeping
  keys (`replicate`, `seed`, `rep`, `trial`, `iteration`). Derive available metric keys from
  observed data. `design_spec.factors` may hint level order/planned levels only for factor
  names the cells actually use; `task_brief.selection_metric` may hint a metric default.
  Neither field is required. Declared names can differ from generated cell keys.
- Show the metric selector only when multiple numeric metrics are available. Route
  unavailable cases (no axes, >3 factors, <2 cells, no numeric metrics, or no scored values
  for the selection) through `HeatmapUnavailable`, with a reason rather than silent `null`.
- Use container queries with the owning `@container`, not viewport breakpoints or threaded
  width props. Preserve narrow-column usability: capped label tracks, truncated/hidden
  label overflow, minimum square widths, and horizontal scrolling. Content-sized labels
  must not push the grid outside a narrow panel.
- Preserve `design_type === 'factorial'` guards on cell views and related canvas chips.
  Unsupported types get an explicit explanation rather than an empty or broken display.
- Preserve row-mode behavior: per-row observations belong in Results; parent scores and
  heatmaps do not apply to a row-mode publication.

## History and maximize

- Design history is a collapsed footnote shown only with at least two revisions. Selecting
  history switches the tally, heatmap, and table together into a read-only view.
  Keep the selection in the owning Cells view and historical query keys separate from current
  data so history cannot overwrite caches used by the canvas or Design controls.
- The legacy CSV download and canvas actions still use the current design, even while history
  is selected; preserve the explicit banner explaining this scope. Do not imply a historical
  export unless its API request actually supplies that scope.
- Historical deletion uses a `Dialog` naming affected counts; it permanently cascades to
  results. The server rejects current revisions and revisions retained by a published version;
  do not offer deletion as a way to remove published results.
- Use the protocol overlay/maximize pattern: Escape/close, restore body scroll, and mount
  the table in exactly one location so sort/page state cannot diverge.
