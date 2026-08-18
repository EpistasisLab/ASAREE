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
  new tab in that panel.

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
  user's `Run`s for `run_metadata.experiment_id` matches (see `ExperimentAgents` in
  `components/protocol/RunsTab.tsx`) and cross-referencing `GET /agents`, not by a new backend
  association. `GET /runs` has no server-side `experiment_id` filter (only `agent_id`) — this
  fetches every run for the user and filters client-side, which is fine at today's scale but
  is the place to add a real filter if a user's run history grows large enough to matter.

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
- **Don't hash/rotate a tint just to break up visual monotony** with no underlying meaning (e.g.
  cycling colors by array index) — that was considered and rejected in favor of the two schemes
  above. If a new list of things genuinely has no status or category worth encoding in color,
  it's fine for it to stay a single accent.
