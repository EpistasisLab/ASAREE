# Managed storage operations

ASAREE keeps the existing ASAREE and Motoro PostgreSQL databases. Original
CSVs, immutable splits and derived datasets remain files. SQLite row indexes
remain supported; replacing them is issue #69. Portable experiment archives
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
| row-indexes | `RESULT_ROW_INDEX_DIR` | result-row-indexes | original SHA-256 SQLite filename, metadata, lock; regenerable but retained during this migration |
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
3. Review `files`, `updates`, `databases` and `problems`. Inventory hashes files
   without mutating storage or either database. It includes source/split
   records, publication/attempt input manifests and reference edges, run
   statuses, result workspace IDs/events, export jobs and Motoro knowledge
   registrations. Symlinks, missing/mismatched artifacts and malformed workspace
   JSON block apply; resolve each explicitly and regenerate the plan. Preserve
   unknown files. Plans contain operational/user metadata: restrict access.
4. With writers still stopped, run:

   ```sh
   python -m asaree.storage_migration apply --plan storage-plan.json
   python -m asaree.storage_migration verify --plan storage-plan.json
   ```

   Apply checks source hashes, copies with verified atomic publication,
   rewrites workspace JSON references, then compares and updates affected
   database fields. It changes neither input identities nor historical pins.
   Existing destination bytes must match. Source files remain untouched.
   The databases have separate transactions: if interrupted between them,
   keep services stopped and rerun the same plan (idempotent) or roll it back.
5. Start API and workers with the same destination settings/mounts. Check a
   pinned historical attempt, a resumable workspace and a knowledge registration
   before reopening traffic. Confirm row-index reads still work. Store the
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
