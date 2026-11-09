# Backend instructions

Read the root instructions first. Paths below are relative to `src/asaree/`.

## Domain and persistence

- Follow the shared [experiment contracts](../../docs/agents/experiment-contracts.md) for
  datasets, design generation, execution/results, and measurement changes.
- Dataset association services do not validate ownership. API/caller boundaries must check
  every dataset against the current user before `set_experiment_datasets`; foreign IDs must
  not attach another user's data. See `_validated_dataset_ids` in `api/experiments.py`.
- Use scoped domain readers instead of direct result-model lookups: caller-supplied revision,
  row, slot, and attempt IDs must belong to the requested experiment/publication, not merely
  exist. Follow `factorial_cells.py`, `factorial_row_results.py`, and the execution/result
  services' existing scope checks when extending an endpoint.

## Verification

From the repository root:

- Lint changed Python paths with `uv run ruff check <changed-paths>`.
- Run affected tests with `uv run pytest <test-path> -q` when their required services exist.
  Inspect fixtures before assuming a test is independent of PostgreSQL or other infrastructure.
- The `stats_validation` marker selects expensive simulations/refits; follow the root approval
  rule before running them or starting extra infrastructure.
- CI uses `uv run mypy src/asaree`, currently warn-only. Report warnings separately from
  passing required checks. The repository deliberately does not require `ruff format --check`;
  avoid unrelated formatting changes.

For migrations, CI integration, and releases, read root `CONTRIBUTING.md` and follow its
validation and approval rules. Do not treat a focused local test as full integration validation.
