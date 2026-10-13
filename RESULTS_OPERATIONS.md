# Scalable execution results

Runs and Results use `/api/experiments/{id}/execution-results`. The canvas uses
`/api/protocols/{id}/run-heads?limit=1` to recover its latest execution. None of
these list reads hydrates Motoro agents, traces, complete outputs, or histories.

Execution and cell pages default to 20 records and cap at 100. Cursors bind the
protocol publication, design revision and filters. Metric cursors also bind the
published summary generation; refresh from the first page after it changes.
Per-row pages generate only the requested window, including unstarted source
rows once their count has been prepared. Stable slot IDs identify completed
records. `unplanned:<replicate UUID>:<row index>` identifies a row without a
persisted slot and cannot be used to inspect an attempt.

Summaries are rebuildable projections. Database triggers record dirty scopes in
`result_refreshes` in the same transaction as result changes. The result worker
coalesces and rebuilds each scope from current facts, streams cell writes, and
publishes cell/overview data atomically. Repeated deliveries do not double-count
attempts. The UI shows records while a summary or usage backfill is preparing.
It refreshes visible pages when a summary generation advances, retains loaded
data on transient failures, and stops polling settled executions. Refresh or
window focus discovers work started by another client after a scope settles.

Operational usage belongs to each production `ProtocolRun`, independently of
declared measurements. Accounting reads scalar, owner-scoped Motoro rows in
batches of 200, including all causally attributed calls (not only final node
pointers). A pointer fallback supports older records. Legacy calls without
either attribution or a retained pointer cannot be recovered. Unreported or
ambiguous default-zero provider fields remain unknown. Execution duration uses
actual start/task completion, excluding queue time. Current execution usage
and all-attempt spend are separate. Immutable measurement/evidence documents
are never rewritten by accounting backfill.

Only explicitly declared built-in runtime measurements enter scalar cell
aggregates and ranking. Global runtime metrics use catalog keys; node runtime
metrics use stable metric IDs with display labels. Display names do not identify
measurements. Reported and legacy values remain opaque, retain
their recorded provenance, and are not coerced or ranked. Per-row outcomes are
never aggregated or ranked. Truncated attempts are excluded from scalar
comparison. Primary ranks are maintained for the whole scope, including pages
after the first.

Selected attempts return bounded previews. Oversized observation values and
artifact payloads carry explicit preview markers; a measured JSON null remains
different from a deferred value. Exact JSON documents are available through
`attempts/{run}/evidence` in chunks of at most 16,384 characters. Node output and
received prompt text use the same chunk bound. Trace pages contain 20 metadata
records; full step evidence loads only after selecting a step. Conversation
messages are paged and large messages are marked previews; complete transcripts
remain available through the recorded JSON reader.

## Durable batch commands

`POST execution-results/jobs` accepts `kind=plan|cancel|export`, a stable
`request_key`, and the selected publication/design query parameters. Creation
returns 202 immediately. Repeating the same owned request key and selection
returns the same job; conflicting reuse is rejected.

A plan first prepares a preview in bounded windows and stores exact parent,
row, slot, and expected-attempt identities. `awaiting_confirmation` includes
the exact planned execution count. Opening a dialog never launches an LLM.
`POST jobs/{id}/confirm` queues that sealed selection. Each small transaction
creates runs and advances their intent checkpoints together. Slots changed
since preview are skipped, so a retry cannot silently re-bill a newer attempt.
Publication/design changes require preparing a new batch. Runs already created
remain pinned to their immutable publications.

Completed executions are skipped unless explicitly selected by slot ID or
`rerun_completed=true`. Explicit selections cap at 100 IDs; an all-scope command
does not send a browser-sized ID list. Failed/cancelled/limit-reached work can be
retried. A plan's cancel action stops remaining planning; already-created runs
continue. The Runs tab's Stop all creates a scoped cancellation command, stops
matching planners and cancels all active attempts, including earlier attempts
that are no longer the latest slot result.

Run cell and Stop cell use the same durable API with `cell_id`. Stopping one
cell excludes it from existing broad planners while allowing their other cells
to continue. Publication predicates also constrain the factor levels and
replicate count: adding cells to a mutable design revision cannot expand the
cohort belonging to an earlier immutable publication.

SQL intent survives worker/Redis loss. Created pending runs are repeatedly
offered to ARQ with the same run job ID; executor guards prevent completed or
cancelled runs from executing again. Delivery continues for committed runs even
if later planning fails or is cancelled. Preview-only cancellation cannot
cancel a confirmed job. Unconfirmed previews expire after 24 hours.

## Exports and source metadata

An export is a background job, limited to one expensive COPY operation globally.
PostgreSQL streams CSV directly to an owned temporary file, followed by atomic
rename. One COPY statement supplies a coherent database snapshot; attempt
selection excludes attempts created after the request cutoff. Active attempts
may acquire evidence before COPY starts, so this is not a historical snapshot
of every field at the request timestamp. A restart repeats the export from its
cutoff. Progress reports bytes, and cancellation is checked during streaming.

CSV v2 uses canonical execution columns and JSON columns for factor values,
usage, original attempt results, node records and imported legacy results.
`legacy_result` preserves imported metric values and artifacts. `attempt_result` contains
exact opaque values, JSON nulls, artifacts and producer provenance. The schema
sidecar lists all columns and metric declarations. This version intentionally
does not flatten arbitrary reported structures into scalar metric columns.
Legacy synchronous CSV retains its old format for small requests; large legacy
full-result requests return 413 with directions to the bounded/job API.

Completed exports are downloaded natively in the browser or streamed to disk
by the SDK. Download capabilities bind job/owner, expire after five minutes,
and cannot supply filesystem paths. CSV/schema files expire after 24 hours.

Original CSV rows are verified by streaming SHA-256 and indexed into an immutable
SQLite cache. Construction uses batches of 1,000 rows and atomic rename.
Subsequent planning windows use random indexed row access instead of reparsing
the original CSV for every retry. Dataset row-schema HTTP reads return 202 with
`ready=false`/`row_count=null` while the worker prepares metadata; they never
read or parse source files in the request. The UI polls that small metadata
record while preparing. Source preparation records hash/CSV validation failures
without exposing paths. Count metadata describes the last verified registered
hash; execution still verifies the pinned source identity before running.

## Deployment and recovery

0. Compose's `db-backup` runs first: when either database has a pending
   migration, it writes `backups/{db}-pre-v{release}-{revision}-{UTC}.dump`
   (the database as it was before that release migrated it) and keeps the newest
   five per database; manual dumps in `backups/` are left alone. If it fails, no
   migration runs. Outside compose, run `python -m asaree.migrations backup`
   before migrating. To restore one (stop the app first):
   `docker compose run --rm --no-deps -e PGPASSWORD=... --entrypoint pg_restore db-backup -h motoro-postgres -U agentic --clean --if-exists --no-owner -d asaree /app/backups/<file>`.
   One error about `transaction_timeout` is expected and harmless (the image's
   client is newer than the server). These are troubleshooting snapshots taken
   only at deploy time, not a backup strategy.
1. Apply Motoro's migrations, then ASAREE's (through `718293a4b5c6`), using the
   usual migration deployment. Historical JSON evidence is not rewritten in migration.
2. The usage and trace reads go through Motoro's `list_run_summaries` and
   `list_run_step_summaries`; their indexes (`ix_agent_runs_run_metadata`,
   `ix_run_steps_run_sequence`) ship with Motoro's own migrations. ASAREE never
   creates indexes on core tables.
3. Run `arq asaree.worker.result_settings.ResultWorkerSettings` alongside the
   production execution worker. Compose defines `asaree-results-worker`, which
   waits for both migration steps. Both workers and the API must share the data
   volume and configured export/row-index paths.
4. Keep `ASAREE_RESULT_EXPORT_DIR` and `ASAREE_RESULT_ROW_INDEX_DIR` on persistent
   shared storage (defaults are `./data/result-exports` and `./data/result-row-indexes`).
5. To requeue all existing overview projections, run
   `python scripts/rebuild_result_summaries.py`. The worker rebuilds from facts
   and retries unfinished accounting; no evidence or execution timestamps change.

Watch `result_refreshes.generation - processed_generation`, refresh `error`,
unfinished `protocol_runs.usage_summary`, pending `result_jobs`, and worker logs
for `result_summary_refresh_failed`, `result_usage_capture_failed`,
`result_command_database_retry`, and `result_command_delivery_failed`.
Size the dedicated result worker separately from LLM concurrency. Commands use
bounded work windows; exports, usage capture and source preparation each have
an independent global advisory lock. Read requests have a five-second SQL
statement deadline and bounded page sizes. Read replicas are not required.

SDK entry points: `execution_summary`, `iter_executions`, `create_result_job`,
`result_job`, `confirm_result_job`, `cancel_result_job`, `iter_evidence_text`,
and `download_result_export`. Supply a pinned publication in `scope` while
iterating and keep the same `request_key` when retrying job creation.

## Validation status

Focused Python/SDK tests, frontend tests, TypeScript, Ruff and the new service
type checks run locally without infrastructure. PostgreSQL validation is
**pending**, as requested; no live migration or production-latency result is
claimed. `tests/test_result_reads.py` applies the complete migration chain in
a unique schema inside a rolled-back transaction when
`ASAREE_RESULTS_TEST_DATABASE_URL` points to a disposable PostgreSQL database.

The opt-in acceptance tests (`ASAREE_RESULTS_RUN_SCALE=1`) exercise 15,786,
100,000 and 1,000,000 runs with 32 KiB node outputs and 8 KiB opaque reports.
They require 20-record pages below 64 KiB, cached summaries below 16 KiB, and
cached summary plus first-page latency below two seconds. Run these tests only
on the disposable database; the million-run case intentionally creates a large
fixture. Before rollout, execute these checks and inspect PostgreSQL plans for
slot lookup and metric ranking, including concurrent execution/outbox updates,
worker restart, Redis interruption, export COPY/download, and downgrade/upgrade.
