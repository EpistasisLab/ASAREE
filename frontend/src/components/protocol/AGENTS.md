# Protocol canvas and experiment panel

Read [frontend instructions](../../../AGENTS.md) before editing this area. Also read these
rules when changing canvas callers in `pages/` or associated helpers in `lib/`.
Before changing datasets, factors, revisions, execution/results, or measurements, read the shared
[experiment contracts](../../../../docs/agents/experiment-contracts.md).

## Navigation and layout

- Experiment tiles open `/experiments/{id}/protocol`. `/experiments/{id}` remains a legacy
  redirect. Keep per-experiment views in the canvas panel or top bar; there is no static
  experiment detail page.
- Keep configuration scores and statistical analysis distinct within Results. Integrate new
  views with the existing panel navigation rather than restoring a static detail page.
- The panel remembers its width in `localStorage` and resizes from its right edge. Preserve
  the navigation rail, minimum content space, maximum width, and minimum remaining canvas
  space enforced by `ExperimentSidePanel.tsx`; use its constants instead of copying widths.
- New panel content responds to its container width using container queries, including
  narrow content columns and maximized overlays.
- For dense views, use an overlay with Escape and a close button, lock body scrolling while
  open, and restore it on close/unmount. Mount stateful content once so sorting/paging cannot
  diverge. Use this maximize pattern rather than the browser Fullscreen API.

## Drafts, production, and datasets

- Clearly show unpublished draft changes and the published version production uses. Preserve
  immutable published execution and read-only historical views per the shared contracts.
- `ProtocolCanvas.tsx` dataset synchronization must seed its comparison ref from the loaded
  graph and never PATCH on mount. A notebook-created experiment can have stored datasets
  without Dataset nodes; mount-time reconciliation would detach them.
- Show unbound declared factors before generation and follow the shared binding contract.
- Runs and Results share `ExecutionBrowser`. Preserve protocol/publication scope in API
  requests, selections, and query keys; use `executionKeys`/existing query helpers in
  `lib/executionQueries.ts`. A historical selection must not display or overwrite current
  publication data. Reuse server summaries and paginated execution/attempt readers rather
  than downloading full run evidence for a list.
- Completed and `limit_reached` executions rerun only on explicit selection; failed/cancelled
  executions remain retryable by the existing flow. Use the shared status helpers instead of
  treating every terminal status as a rerun. Preserve confirmations because execution costs money.
- Before editing the legacy Cells components or their aggregation helpers, read
  [Cells instructions](cells/AGENTS.md). They are not the current Runs/Results browser.

## Canvas node colors

- Both node cards and inspectors call `nodeAccent(kind)` from `lib/nodeAccent.ts` with the
  same key. Add explicit slots for new kinds; unknown kinds fall back to `--primary`.
- Preserve the established `--chart-*` anchors: agent, dataset, reason+act, critic gate,
  and the Model family. Ask before changing an anchor. Resolve collisions by moving other
  kinds into `--node-*` tokens in `index.css`, not by repainting established anchors.
- Model providers share one family hue; icons/labels distinguish providers. Related MCP and
  OKF kinds intentionally have adjacent hues. Follow the current map rather than hashing kinds.
- Connector captions use the separate yellow `--node-label` token to stand out from their
  node, rather than inheriting `--card-accent`.
