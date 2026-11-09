# Shared experiment contracts

Read before changing experiment datasets, agents, factors, revisions, execution/results,
or measurement behavior in the backend, frontend, SDK, or tests. Paths below are
relative to the repository root; bare service filenames refer to `src/asaree/services/`.
Vocabulary is defined in `CONTEXT.md`.

## Datasets

- `experiment_datasets` is the stored many-to-many source of truth. Read/write through
  `src/asaree/services/experiments.py`: `set_experiment_datasets`,
  `get_experiment_dataset_ids`, and batch `get_dataset_ids_by_experiment` for lists.
  Preserve join-row `position`: it is canvas wiring order and agent prompt order.
- `dataset_ids` fully replaces the association; `[]` detaches everything. The compatibility
  scalar `dataset_id` is a one-dataset write shorthand and a read view of `dataset_ids[0]`.
  The former scalar model FK is dropped; do not create another source of truth.
- Creation and `PATCH /experiments/{id}` both accept dataset associations. Notebook workflows
  can create the experiment before registering datasets, then attach them with PATCH. Preserve
  both paths and validate ownership before writing associations. No datasets is a valid,
  permanent state; do not backfill old experiments speculatively.
- Frontend graph synchronization must never detach stored datasets on initial load. See
  [protocol instructions](../../frontend/src/components/protocol/AGENTS.md) before editing it.

## Agents

- Agents are reusable per-user templates, not a stored experiment association. There is no
  `experiment_agents` join table; do not introduce one for membership displays.
- For a participation display, derive membership from experiment-scoped execution records,
  not a new stored relationship. The agent-run API supports server-side `metadata` JSON
  filtering (including `experiment_id`); filter before applying a limit rather than fetching
  an arbitrary run page and filtering it client-side. See `src/asaree/api/runs.py`.

## Cells and design history

- A cell is one unique factor combination with its planned replicates. Group replicate
  responses by `cell_id`. Run/progress/scored counts count replicates. “Run all cells”
  runs every pending replicate across the cells.
- Cells belong to a design revision. The current revision has `superseded_at IS NULL`;
  a partial unique index enforces at most one current revision per experiment.
- Access `FactorialReplicateResult` through `src/asaree/services/factorial_cells.py`'s
  `get_replicate`, `list_replicates`, or `upsert_replicate`. Queries must join the owning
  cell and scope both experiment and revision, including when `revision_id` is supplied
  by a caller. Default to the current revision; read historical revisions explicitly.
- `generate_design_cells` in `src/asaree/services/design_generation.py` preserves an
  unpublished revision only while no planned replicate slots are removed and the coordination
  strategy is unchanged. Shrinking replicate count can supersede even if no cell disappears.
  A strategy change starts clean; it must not reuse results from a different execution model.
- Once a publication references a design, changed declarations or planned slots create a
  separate draft revision. Repeating an unchanged declaration reuses the design. Never mutate
  a published design to accommodate a seed, level, or replicate-count edit.
- Carry results forward only on compatible unpublished design changes, matching factor-value
  identity and replicate number, not display labels alone. Strategy changes and changes to a
  published design do not carry results forward. Originals remain in their historical scope.
- `ProtocolRun.design_revision_id` pins planning, execution, and result write-back so
  mid-flight regeneration cannot redirect results. Run references use `ondelete="SET NULL"`;
  cells cascade when their revision is deleted, allowing runs to outlive designs.
- Reject deleting the current revision or any design referenced by a published version with
  409. Other historical deletion is permanent and cascades to results. Regenerate to replace
  the current design; use `design_revisions.py` for numbering and retention rules.

## Published execution and factor bindings

- `Protocol.graph` is the autosaved draft. A published experiment version freezes the graph,
  experiment settings (including measurement configuration), and design reference together.
  Use `protocol_revisions.py`/`experiment_versions.py`; a graph-only comparison cannot decide
  whether an experiment has unpublished changes. Production uses a published version, while
  canvas Test Runs and node Play have their own preview paths.
- A `ProtocolRun` records both protocol and design revision IDs. Workers load the published
  snapshot so editing the canvas cannot alter queued, running, or resumed work.
- A declared factor must bind to at least one canvas field before a cell batch can run.
  Unbinding/deleting a field leaves the factor declared and unbound. Require rebind/removal,
  design-impact review, and regeneration. `src/asaree/services/factor_bindings.py` is the
  shared backend guard; the frontend shows the same invalid state before generation.

## Results and measurement

- A replicate, a row execution, and an attempt are different identities. Row mode creates
  one execution slot per original dataset row under a parent replicate; rows are not factors
  or extra stochastic replicates. Keep experiment, protocol publication, design, and row/slot
  scope together in readers and write-back. Counts must name which identity they count.
- `ProtocolRun.attempt_result` retains an attempt's own facts; replicate/row records project
  the latest attempt. Rerunning must not replace an older attempt's recorded measurements
  with the latest projection. Use execution/attempt readers for history.
- Built-in metrics are evaluated from attempt snapshots and support comparison/aggregation.
  Custom metrics report opaque values already produced by the configured Agent or its last
  matching Script/MCP call; do not invoke an extra evaluator, coerce them to scalar scores,
  or rank/aggregate them. Preserve producer provenance and observation status: measured JSON
  `null` differs from unavailable. See `CONTEXT.md`,
  `src/asaree/services/measurement_engine.py`, and the available
  ADR `docs/adr/0001-measurements-have-explicit-producers.md` for measurement changes.
