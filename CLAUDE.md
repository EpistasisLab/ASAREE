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
  key=value data dumps use `font-mono` (see `components/protocol/cells/CellsTable.tsx`,
  `ApiTokensSection.tsx`)
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
  - Detail on the Experiments tile grid lives in `frontend/src/pages/CLAUDE.md`; detail on
    the Cells heatmap/table (and the per-agent run tally that replaced the old Agents grid)
    lives in `frontend/src/components/protocol/cells/CLAUDE.md`. Read the relevant one before
    touching `ExperimentsPage.tsx` or anything under `components/protocol/cells/`.
- **There is no separate experiment detail page** — clicking an Experiments tile lands on
  `/experiments/{id}/protocol`, the protocol canvas, and everything that used to be on a
  static `ExperimentDetailPage` now lives in that canvas's `ExperimentSidePanel` tabs
  (Design/Cells/Runs/Results) or its top bar. `/experiments/{id}` is kept only as a redirect
  for old bookmarks. Don't reintroduce a static detail page; a new per-experiment view is a
  new tab in that panel. The panel is drag-resizable by its right edge (320px–1100px, always
  leaving the canvas ≥420px) and remembers its width in `localStorage` — so a new tab in it
  should be width-responsive via container queries, not built for one fixed column width.

# Git commit conventions

Do not add `Co-Authored-By` or `Generated-with` lines to commits or PRs.

# Cost-conscious execution

Favor the cheapest tool/approach that reliably gets the job done. Before doing any of the
following, say in one sentence what you're about to do and why it's needed, and ask first
rather than doing it silently as a routine part of coding:

- **No Playwright/browser automation or screenshot-based UI verification.** The user checks
  UI changes visually themselves once you report them done — see the standing preference to
  skip Playwright verification agents. Screenshots are read as images, which cost far more
  tokens than text, and driving a browser adds many extra tool round-trips on top. Verify with
  `tsc`/`oxlint`/existing tests instead; if something genuinely can't be confirmed without a
  browser, say so explicitly rather than skipping verification or reaching for Playwright
  unasked.
- **No multi-agent fan-out for routine coding work** — don't invoke the `Workflow` tool or
  spawn multiple subagents in parallel just because a task touches several files. Each spawned
  agent carries its own multiplied context/token cost. Default to working solo, or at most one
  scoped `Explore`/`general-purpose` agent for a targeted search. Reserve `Workflow`/parallel
  fan-out for when the user explicitly asks for that scale.
- **No unscoped, repo-wide exploration** ("search everywhere," reading many files in full) when
  the request already names specific files or areas — grep/read narrowly first, and only widen
  the search if that comes up empty.
- **No speculative full test-suite runs or long builds** — run the narrowest test/lint command
  that covers the change under review; ask before running something that takes minutes or spins
  up extra infrastructure (dev servers, containers, etc.).

# Experiment data model

- **Datasets are a real, stored many-to-many** — the `experiment_datasets` join table
  (`models/experiment_dataset.py`, migration `d5a3b90c71e4`), unlike agents below: a dataset
  genuinely is a first-class property of an experiment worth a real relationship, matching
  what ARES does. It started as a scalar `ResearchExperiment.dataset_id` FK (migration
  `a1b2c3d4e5f6`); that column is **dropped** — uncapping the Dataset connector means one
  experiment can run against several, so a single winner would have been a lie. Read/write it
  through `services/experiments.py`'s `set_experiment_datasets` /
  `get_experiment_dataset_ids` / `get_dataset_ids_by_experiment` (the batch one — use it on
  list endpoints to avoid an N+1), never by touching the model.
  - `position` on the join row preserves **canvas wiring order**, which is the order the
    agent's prompt lists the datasets in, so it's user-visible, not cosmetic.
  - `dataset_ids` is a **full replacement**, not a merge (`[]` detaches everything). The API
    and SDK still accept and return the old scalar `dataset_id`: on write it's the
    one-dataset shorthand, on read a view of `dataset_ids[0]`. Don't add a second source of
    truth — the join table is it.
  - It's set via `PATCH /experiments/{id}`, not at creation: the notebook's Step 1 (create the
    experiment) runs *before* Step 2 (register the dataset), so there's nothing to attach yet
    at create time — see the `client.experiments.update(...)` call after Step 2 in
    `spinal_pipeline.ipynb`. In the GUI it's `ProtocolCanvas.tsx`'s `syncExperimentDatasets`
    effect, which PATCHes whenever the canvas's set of Dataset nodes changes. That effect's
    ref is deliberately **seeded from the graph as loaded so it never fires on mount** — a
    mount-time "reconcile" would see zero Dataset nodes on a notebook-driven experiment and
    detach its dataset. Keep that property if you touch it.
  - An experiment with no datasets attached is an expected, permanent state (everything
    created before the FK existed) — not a bug to backfill.
- **Agents are deliberately NOT a stored relationship** — there's no `experiment_agents` join
  table, and none is planned. Agents are reusable per-user templates, not something an
  experiment "owns"; asking "which agents ran in this experiment" is answered by scanning the
  user's `Run`s for `run_metadata.experiment_id` matches (see `ExperimentAgents` in
  `components/protocol/RunsTab.tsx`) and cross-referencing `GET /agents`, not by a new backend
  association. `GET /runs` has no server-side `experiment_id` filter (only `agent_id`) — this
  fetches every run for the user and filters client-side, which is fine at today's scale but
  is the place to add a real filter if a user's run history grows large enough to matter.
- **Cells belong to a design revision, not to the experiment** — `experiment_design_revisions`
  (`models/experiment_design_revision.py`, migration `e2f7c4a91b60`). The current design is the
  one revision with `superseded_at IS NULL` (a partial unique index enforces "at most one",
  rather than convention). This exists because generation used to be purely additive: a design
  shrunk from 6 cells to 2 left all 6 behind, so the experiment still read "0/6 scored" and
  "run all cells" still launched 6.
  - **Never query `FactorialCellResult` filtered on `experiment_id` alone** — that sees every
    superseded design's cells, which is exactly the bug. Go through
    `services/factorial_cells.py`'s `get_cell`/`list_cells`/`upsert_cell`, which scope to the
    current revision by default and take an explicit `revision_id` only to read history on
    purpose. `experiment_id` stays denormalized on the cell (most queries are per-experiment)
    and both filters are applied together, since `revision_id` can arrive from a query string.
  - `generate_design_cells` opens a new revision **only when the new design would drop a cell
    the current one has**. Re-clicking generate, changing the seed, widening a factor's levels
    or raising `replicates` all keep the current revision and its row ids — history entries are
    meant to mark designs that actually discarded something, not every edit. Results for a
    label the two revisions share are copied forward; the originals stay in history.
  - `ProtocolRun.design_revision_id` **pins** which design a run's result belongs to, so a
    regenerate mid-flight can't redirect the write-back (`plan_cell_runs` →
    `run_protocol`/`promote_cell_score_metrics`). It's `ondelete="SET NULL"` — run history
    outlives the design; cells are `ondelete="CASCADE"` so deleting a revision really does
    delete its results.
  - Deleting the **current** revision is refused (409) — that's a reset, not a deletion;
    regenerating is how you replace it. Revision numbers are `max()+1` over every revision ever,
    so a deleted number is never reused. The history UI is `components/protocol/cells/
    DesignHistory.tsx`.
- **A protocol canvas is a draft; production runs use a published revision** — `Protocol.graph`
  remains the autosaved draft while `protocol_revisions` stores immutable graph snapshots. A
  `ProtocolRun` carries both `design_revision_id` and `protocol_revision_id`; `run_protocol`
  loads the latter, so a canvas edit can never hot-patch queued, running, or resumed work. Never
  create a production run from `Protocol.graph` directly: publish first and pass the resulting
  revision through the planning path. The top bar deliberately says when a visible canvas has
  unpublished changes and which published revision production currently uses.
- **A declared factor must bind to at least one canvas field before a cell batch can run** —
  deleting/unbinding a node field leaves the factor declared but *unbound*, rather than silently
  deleting the experimental treatment. The user must rebind it or remove it, then review the
  design impact and regenerate. `services.factor_bindings` is the shared backend guard; the
  Design tab shows the same state before generation.

# Color — meaningful variation, not decoration

A single cyan accent everywhere read as flat/monotone rather than retrofuturist (that style
leans on multi-hue neon contrast — synthwave, Tron, Blade Runner — not minimalism). Cards can
now be individually re-tinted, but the tint must mean something; don't add color for its own
sake.

- **Mechanism**: `components/ui/card.tsx`'s `Card` reads a `--card-accent` CSS custom property
  (falling back to `--primary`) for its ring/glow/corner-bracket colors — see the comment on
  `Card` itself. Set it via `style={cardAccent('var(--chart-3)')}` (`lib/utils.ts`) on any
  individual `Card`; anything that also wants the icon/badge/hover-arrow to match reads
  `text-[color:var(--card-accent,var(--primary))]` the same way `AgentCard` and the Experiments
  tiles do — don't hardcode `text-primary` on elements living inside a re-tintable card.
- **Status-driven tint** (`lib/experiment.ts`'s `cellsStatusAccent`): `--chart-4` (amber) = cells
  generated but none scored, `--primary` (cyan) = partially scored, `--chart-3` (emerald) = fully
  scored, `--muted-foreground` (dim) = no cells yet. Used on both the Experiments-list tiles and
  the detail page's "Cells" stat card — the same status must always resolve to the same color
  everywhere, so change it in that one function, not per call site.
- **Hash-driven tint** (`lib/utils.ts`'s `hashToChartHue`): for things with no "done/pending"
  status but real category variety — e.g. `AgentCard` tints by `model_config.model`, so agents
  sharing an LLM visually match without a hardcoded model→color table that goes stale the moment
  a new model ships. Same input always produces the same one of the five `--chart-*` hues.
  **Not for protocol-canvas nodes** — see the table below.
- **Table-driven tint** (`lib/nodeAccent.ts`'s `nodeAccent(kind)`): protocol-canvas nodes only.
  Thirteen node kinds against five `--chart-*` hues made collisions arithmetic, and they landed
  on the confusable pairs (Skill/AI, Dataset/Knowledge, Pattern/Script), so every kind now has an
  explicit entry. Five keep the `--chart-*` hue the old hash gave them (agent, dataset,
  reason+act, critic gate, and the LLM family) and the other eight moved to a `--node-1`…`--node-8`
  slot in `index.css`'s `.dark` block — **fixing a repeat means moving the bucket-mates, not
  repainting the canvas**, so don't reassign an anchor's hue without being asked. Both the node
  card and its inspector call `nodeAccent` with the same key so they can't drift; a kind with no
  entry falls back to `--primary` rather than a hashed hue, so a new node type visibly asks for a
  slot. Adjacent hues are assigned to related kinds on purpose (the two MCP kinds, the two OKF
  kinds). Note LLM nodes are one hue for the whole family, not one per provider.
  - `--node-label` (yellow) is separate from all of them: it's the connector captions on
    `AgentNode`/`CriticGateNode`, which are meant to stand out *from* their node, so they
    deliberately don't follow `--card-accent` the way everything else inside a card does.
- **Don't hash/rotate a tint just to break up visual monotony** with no underlying meaning (e.g.
  cycling colors by array index) — that was considered and rejected in favor of the schemes
  above. If a new list of things genuinely has no status or category worth encoding in color,
  it's fine for it to stay a single accent.

# Communication style

Be concise and execution-oriented.

For coding tasks:
- Do the requested work without lengthy explanations.
- Make routine implementation decisions yourself.
- Do not present multiple alternatives unless the choice materially affects
  architecture, correctness, or requirements.
- Do not enumerate pros and cons for routine decisions.
- Do not explain obvious code changes.
- Do not repeatedly summarize what you are doing.
- Ask questions only when ambiguity would materially change the implementation.
- Prefer implementing over discussing.
- After completing a task, give a short summary of what changed.
- If you identify an important concern, state it briefly and recommend one
  course of action rather than presenting many possibilities.

Keep responses focused, brief, and concise. Give deeper explanations only
when I explicitly ask for them.
