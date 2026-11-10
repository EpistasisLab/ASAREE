# Managed storage operations

ASAREE keeps the existing ASAREE and Motoro PostgreSQL databases. Original
CSVs, immutable splits and derived datasets remain files. Derived row indexes
live in ASAREE PostgreSQL. Portable experiment archives
are issue #55. See [ADR 0003](adr/0003-immutable-dataset-inputs-and-managed-artifacts.md).

## Configuration and inventory

Set `ASAREE_DATA_ROOT` to an absolute, shared filesystem mount in every API,
execution worker and results worker. Its compatibility default is `./data`,
resolved against the service's startup directory. Do not start these services
from different directories with a relative default. Application settings use
explicit constructor values, process environment, settings-file values, then
defaults, in that order. A nonempty directory override takes precedence over
the root, even when the root changes. Relative overrides are resolved against
the startup directory; `~` is expanded. Empty overrides select the root's
default child. API/worker settings export resolved absolute storage paths to
the existing Motoro MCP subprocess allowlist. Standalone workspace-core reads
the same environment; explicit `Workspace(root=...)` takes precedence.

| Namespace | Override (`ASAREE_` prefix) | Default child | Persisted references / lifecycle |
| --- | --- | --- | --- |
| datasets | `DATASET_STORAGE_DIR` | datasets | `dataset_artifacts` UUID, namespace, key, hash, size, status; source/split FKs; immutable inputs |
| workspaces | `DATASET_WORKSPACE_DIR` | workspaces | result workspace IDs, events, state formats 1/2, named slots, manifests, `dataset_inputs.json`; reusable state and durable outputs |
| exports | `RESULT_EXPORT_DIR` | result-exports | `result_jobs` UUID; CSV and schema sidecar; existing export expiry |
| documents | `OKF_DOCUMENT_DIR` | okf-documents | Motoro knowledge server command; mutable managed knowledge |
| bundles | `OKF_BUNDLE_UPLOAD_DIR` | okf-bundles | Motoro knowledge server command; mutable managed knowledge |
| backups | `BACKUP_DIR` | backups | pre-migration PostgreSQL dumps; explicit CLI `--dir` wins |
| scratch | `SCRATCH_DIR` | scratch | reserved operator scratch location; temporary script contexts remain beside their consuming workspace |

Additional storage outside this table:

- External knowledge bundles are user-owned. `ASAREE_OKF_BUNDLE_ROOT` controls
  browsing/new registrations, separately from managed storage; the local
  compatibility default remains `~`. Compose defaults to
  `/app/data/external-bundles`, configurable explicitly. Existing absolute
  registrations continue to work even outside the new browsing root. Move
  external files only with their owner's agreement and explicitly update their
  registration; never convert them to owned files based on their location.
- Compose's existing `./backups:/app/backups` mount and explicit backup
  `--dir /app/backups` remain overrides. Include these dumps in backup inventory.
- PostgreSQL volumes contain both databases; Redis contains queues, working
  memory and other runtime state. Uploaded skills and experiment artifacts
  stored as database content are covered by database backups, not file roots.
- Script sources are published graph content; materialized scripts, inline
  execution contexts and compatibility directories live under their workspace
  or the OS temporary directory. Treat unknown script outputs as durable until
  reviewed, rather than assuming everything in a run directory is scratch.
- Legacy row snapshots lived at the dataset directory's parent under
  `_protocol_runs`. Inventory that location explicitly; new row snapshots and
  scripts share the configured workspace root. They keep the same attempt and
  Agent identities, row projection and source hash.

## References, readiness and retention

The existing artifact contract remains namespace + relative storage key with
content identity verified separately. `managed://<namespace>/<key>` is its
filesystem spelling in workspace state and knowledge commands. Dataset UUIDs,
artifact UUIDs, split UUIDs, publication/attempt pins, binding order and input
scope are unchanged. Runtime readers resolve references at use. Workspace APIs
continue to return resolved paths; formats 1 and 2 and external seed paths
remain readable. Legacy absolute paths require the explicit migration below
before their old filesystem is removed. Do not infer historical split choices
or split provenance during migration. Effective legacy target handling remains
the existing dataset/pinned-target contract.

Whole-dataset sharing rules and attempt isolation stay those of #68. Stable
workspace IDs and slots are retained, not recomputed from a directory name or
content hash. Structured MCP operations retain their scope guards. Row snapshots
are immutable within an attempt and checked against their exact projection.

Raw uploads, state, manifests, scripts and export metadata publish through
unique sibling staging files and atomic publication. Workspace stage pairs are
prepared and verified together before rename, then state advances. First stage
writes retain the established directory layout; replacements get a distinct
generation so a failed write cannot overwrite the prior readable pair.
Immutable source/split artifact records become ready only after bytes exist.
No reader uses `.preparing`, `.part`, `.building`, or `.partial` files as ready
input. On interruption, retain the previous pointer, inventory partial files,
and retry the owning operation. Never promote a partial file by renaming it
manually. If an input pin's file is missing/mismatched, repair from verified
backup; execution fails explicitly and does not select the current split.

Retention policy:

- Immutable sources and every split are retained while their dataset exists.
  Dataset deletion checks publication/attempt reference edges and retained
  workspace seed references, including legacy state. Unknown/unreadable
  workspace state blocks input deletion. Inspect it before retrying.
- PostgreSQL row indexes are retained for the lifetime of their registration,
  then cascade with it. Pending, queued and awaiting-confirmation result jobs
  protect their driver even when they predate publication/attempt pins.
  Terminal job previews release this extra protection; publication, attempt and
  workspace protections remain independent. No age-based row-index collector runs.
- Reusable workspaces, derived generations, durable outputs and knowledge
  copies are retained by default. Unregistering knowledge removes its Motoro
  registration only, including for uploads. This intentionally favors history
  and resume protection over automatic reclamation. External files are never
  removed. Upload path containment is not ownership evidence.
- Existing result-export retention and token expiry remain in force. The
  results worker deletes files only for expired terminal export jobs, using
  their exact job IDs. It does not apply export expiry to durable workspace
  outputs or database experiment artifacts.
- Pre-migration automatic dumps retain the configured `--keep` per database;
  manual dumps remain untouched. Preserve an independent coordinated backup.
- Scratch/abandoned generations are cleanup candidates only after the owning
  operation is stopped and database plus retained-workspace references have
  been inspected. Active/resumable jobs, historical attempts and publications
  protect their dependencies regardless of age. There is deliberately no
  directory-name-based orphan collector. The migration never deletes sources,
  unknown files, row indexes, lock files, or interrupted writes.

## Copy-first migration and rollback

Run this as an offline maintenance operation with every API, worker, MCP
subprocess and external knowledge writer stopped. Keep the old image, settings
and mounts. Obtain coordinated backups first. Run the tool using the new image
and intended destination configuration, with both database URLs configured.

1. Create `old-roots.json` listing **every** namespace in the table above and
   its absolute old directory (including overrides and the old dump location).
   Give the destination a fresh root with enough space for a full copy; use
   fresh destination overrides as necessary. Do not nest destinations beneath
   sources. Unchanged namespaces can be backed up and copied to a fresh
   destination override for this operation. Do not clear existing directories.
2. Run the dry-run inventory, saving its owner-only plan outside either tree:

   ```sh
   python -m asaree.storage_migration inventory --old-roots old-roots.json --plan storage-plan.json
   ```

   Add `--legacy-attempt-root /absolute/old/data/_protocol_runs` if that
   legacy location exists. For each knowledge upload whose ownership has been
   confirmed from registration/upload evidence, add
   `--managed-knowledge <Motoro-server-UUID>`. Unlisted commands remain external
   absolute references; inventory and retain their mounts. Do not infer upload
   ownership from directory names. Review the plan's database inventory for
   all external commands and legacy paths; include their owned locations in
   the coordinated backup with the appropriate permission.
3. Review `files`, `updates`, `databases`, `diagnostics` and `problems`. Inventory hashes files
   without mutating storage or either database. It includes source/split
   records, publication/attempt input manifests and reference edges, run
   statuses, result workspace IDs/events, export jobs and Motoro knowledge
   registrations and AgentRun ambient metadata/native snapshots. Historical
   artifacts already marked missing or unavailable remain unchanged and appear
   in diagnostics; their pinned inputs remain unavailable. Ready artifacts with
   missing files or mismatched bytes, pin identity conflicts, symlinks and
   malformed workspace state JSON block apply; resolve each explicitly and regenerate the plan. Preserve
   unknown files. Plans contain operational/user metadata: restrict access.
4. With writers still stopped, run:

   ```sh
   python -m asaree.storage_migration apply --plan storage-plan.json
   python -m asaree.storage_migration verify --plan storage-plan.json
   ```

   Apply checks source hashes, copies with verified atomic publication,
   rewrites only version-record locators in recognized workspace state and
   documented ambient locator fields in Motoro run metadata/native snapshots,
   then compares and updates affected
   database fields. It changes neither input identities nor historical pins.
   Row-view values, input names, learned payloads, provenance and arbitrary JSON
   files retain their exact values; opaque JSON files are copied byte for byte.
   Existing destination bytes must match. Source files remain untouched.
   The databases have separate transactions: if interrupted between them,
   keep services stopped and rerun the same plan (idempotent) or roll it back.
5. Start API and workers with the same destination settings/mounts. Check a
   pinned historical attempt, a resumable workspace and a knowledge registration
   before reopening traffic. Confirm PostgreSQL row-index reads work. Store the
   plan and operator validation with the coordinated backup. Retain the source
   tree until restore and resume validation are complete.

For rollback, stop writers again, run
`python -m asaree.storage_migration rollback --plan storage-plan.json` against
the same databases, then restore the previous image/configuration/mounts and
restart using the untouched source tree. Rollback compares exact database
values before restoring them and leaves destination files intact for diagnosis.
If new work was accepted after cutover, reconcile it or restore the complete
coordinated backup; switching to the old tree alone would lose that work.
Never downgrade only one database or roll back file bytes independently of
their database references.

## Retiring legacy SQLite row indexes

`ASAREE_RESULT_ROW_INDEX_DIR` is no longer a runtime setting. Its files are
derived data, not authoritative inputs. Keep the previous directory/mount and
old settings in your coordinated backup until this offline migration and
resume validation finish. Do not add its directory to a new storage relocation
plan; finish or roll back any old relocation plan with its original application
version first. New relocation plans use the namespace table above.

With APIs, workers and MCP writers stopped, upgrade ASAREE migrations, then
run the following commands in the new image with the ASAREE database configured.
Pass `--legacy-dir` once for every previous default/override location. Use an
existing empty directory if there were no SQLite indexes but saved jobs exist.

```sh
python -m asaree.row_index_migration inventory --legacy-dir /old/result-row-indexes --report row-inventory.json
python -m asaree.row_index_migration rebuild --legacy-dir /old/result-row-indexes --report row-rebuild.json
python -m asaree.row_index_migration verify --legacy-dir /old/result-row-indexes --report row-verified.json
```

Inventory reads SQLite only in read-only mode, hashes the originals, parses
them with the recorded v1 CSV rules and compares every legacy row/position and
header in bounded batches. It writes a private report, without changing files
or database rows. Rebuild creates a separate PostgreSQL index per registration,
never globally sharing by hash/owner. It verifies every row against originals
and SQLite before converting active saved job progress to dataset UUID, source
hash, parser version and count. Unprepared legacy jobs remain protected.
Missing originals, changed hashes, inconsistent rows, unsupported parser
versions and unavailable job registrations are explicit failures. Terminal job
evidence appears in diagnostics and does not block unrelated work. Unassociated
indexes, locks, partial writes, symlinks and unknown files remain untouched;
review them separately. A mismatched legacy index must be retained for diagnosis
or moved aside explicitly by the operator before rebuilding from its verified
original; migration never trusts it instead of the original.

Review the reports and repair failures using verified backups. No historical
identity or source is inferred from a filename. Once verification passes and
you have validated a pinned attempt and resumed batch, stop writers again and
optionally run:

```sh
python -m asaree.row_index_migration retire --legacy-dir /old/result-row-indexes --report row-retired.json
```

Retire re-verifies originals, PostgreSQL and SQLite, preflights file hashes and
destination conflicts, then renames verified `.sqlite` files to `.sqlite.retired`.
It never deletes them or their lock files. Retain retired files with the backup;
manual removal is a separate operator decision. On interruption inspect the
report/files before retrying. A rollback uses the coordinated pre-cutover
database/file backup and old image/settings, renaming retired files back if
needed. New source descriptors omit legacy columns, so running the old image
against newly accepted job progress is not a supported rollback.

The index stores UTF-8 JSON bytes in BYTEA, preserving exact column order,
NULs, empty strings, leading zeros, Unicode and multiline values. Parser
`csv-utf8-sig-strict-v1` keeps the existing BOM, strict CSV, distinct nonempty
header, equal field-count and skipped-empty-record rules. Job descriptors omit
headers/values; schema cache headers containing NUL use a lossless binary
encoding decoded at the API boundary. Existing execution graph/attempt JSONB
fields still cannot represent NUL strings; this migration does not change that
pre-existing execution limit or escape/change user strings to bypass it.

Ready indexes always check their source registration and current original.
Each new job, worker tick and process restart hashes original bytes; a job may
reuse verification only for its bounded windows in that tick while file
device/inode/size/modification/change times agree. A change invalidates the
lease. Verification runs before progress/cancellation row locks. Ready indexes
need no temporary copy; ingestion parses a verified temporary snapshot and
publishes metadata and rows in one transaction under a PostgreSQL source lock.

## Coordinated backup and restore

Quiesce all writers to establish a common consistency boundary. Dump **both**
ASAREE and Motoro PostgreSQL databases with `pg_dump --format=custom --no-owner`.
Record schema revisions, application/Motoro versions, storage configuration,
artifact inventory/hashes and timestamp. Copy/snapshot all managed namespaces,
explicit external bundle locations with owner permission, and the separate
Compose backup mount. Preserve permissions and relative keys. Encrypt and
restrict database dumps and plans; they contain private data. The automatic
pre-migration dump command runs only when schema changes are pending and is
not a complete backup of files or an unconditional migration backup.

Redis is runtime state, not the authority for immutable input identity. For a
resume-capable backup, stop dequeuing and snapshot Redis at the same boundary
as the databases/files, retaining queues, working memory, cancellation and
authentication/cache state according to the deployment's Redis policy. If
Redis is intentionally discarded, reconcile pending/running jobs from
PostgreSQL and their retained workspace state before admitting new traffic;
do not claim transparent Agent resume, and do not replay a queue against
unrelated database/file snapshots. Result jobs have their existing relay path.

Restore into an isolated deployment with services stopped: restore both
databases using the recorded versions/schema revisions, restore every managed
root and required external mount, then restore matching Redis state or perform
the explicit runtime reconciliation above. Configure all services identically,
verify file hashes and reference resolution, and test pinned execution and
workspace resume before reopening access. A storage root rename alone is not
a migration, and an experiment export is not a deployment backup.
