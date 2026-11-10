# Published experiment packages

The canvas top bar offers **Download experiment package**. Select a published
version, including an older version, and optionally include its results. The
separate **Download draft definition JSON** action remains a draft definition
export without dependency files.

API clients create the same owned export job used by Results CSV downloads:

```http
POST /api/experiments/{experiment_id}/execution-results/jobs?protocol_id={protocol_id}&protocol_revision_id={publication_id}
Content-Type: application/json

{"kind":"export","format":"experiment_package","include_results":false,"request_key":"unique-client-request"}
```

The publication ID is mandatory. Partial cell, replicate, or row selections are
not supported for a package. Poll the returned job ID through the existing job
endpoint. Completed jobs provide a native `download_url`, a manifest
`schema_url`, and `progress.sha256` for the compressed archive. Links are signed
for five minutes; requesting job status renews the link. Prepared files expire
24 hours after job creation. Cancellation and failure never publish partial
archives as completed downloads.

## Schema versions and compatibility

New exports use schema **2**. Imports accept schemas **1 and 2**; other versions
are rejected before confirmation. Version 2 adds structured Skill metadata,
Skill file encodings/order, dataset registration metadata, publication notes and
stable row-slot evidence. These additions close reconstruction gaps found by
auditing the actual version 1 exporter. Version 1 files remain readable and
re-exportable; the importer parses recorded Skill frontmatter, but never claims
to recover omitted source filenames, original resource encodings/order or row
slot facts. Such omissions are explicitly reported in preflight. A v1 binary
Skill resource is represented locally as base64 without claiming that was its
original storage encoding.

The common archive layout is:

The `.tar.gz` archive contains:

| Member | Meaning |
| --- | --- |
| `manifest.json` | Format/version, selected publication, capture window, software requirements, completeness and inventory hashes |
| `files.jsonl` | One record per payload member: relative path, source identity, byte count, SHA-256 and provenance |
| `dependencies.jsonl` | Included, missing, unresolved and intentionally excluded dependencies, with reasons |
| `publication/` | Selected canvas and experiment settings, including factors, bindings and measurements |
| `experiment-metadata.json` | Current name/description, explicitly captured at download time |
| `design/` | Referenced design declaration and generated cell/replicate identities |
| `inputs/` | Pinned dataset bindings, originals, exact split files and split provenance |
| `resources/` | Scripts, rendered stored Skills with all bundled resources, and authorized knowledge content |
| `requirements/` | Model configurations and MCP server/tool requirements without connection secrets |
| `results/` | Optional version-scoped attempts, observations, recorded evidence and retained attributable files |
| `sources/` | Imported source manifests/publications and typed identity mappings; with results selected, the exact original source archive |

The manifest hashes both inventories. `files.jsonl` hashes dependency inventory
bytes but does not hash itself or the manifest, avoiding circular references.
Dataset artifact UUIDs map to paths through the dependency inventory. Arrays in
pinned bindings preserve their recorded ordering and row projections. Resources
are deduplicated within this export by stable identity, never across owners.

`publication_pinned` identifies recorded publication/configuration and verified
input identities. `captured_at_download` identifies mutable reusable library or
knowledge content. Skill entry points are rendered from stored metadata/body;
bundled UTF-8 and base64 assets retain their stored content. `recorded_attempt`
identifies an attempt's own facts, rather than a latest-attempt projection.
`legacy.unknown` records history that cannot be established. Missing historical
settings, sources, splits or files never resolve to the current draft or split.

ASAREE records are read under one repeatable-read snapshot. Motoro resources use
a separate snapshot; filesystem captures verify opened files and detect tree
changes. Mutable external knowledge has no cross-file transaction or historical
snapshot. The manifest records this limitation rather than claiming publication
fidelity. Externally registered directories must be authorized through ownership
and isolated from platform storage; broad roots, other owners' knowledge,
symlinks and ambiguous overlaps are reported as unresolved dependencies.

Credential/connection fields, including typed fallback and sampling snapshot
fields, and recognized credential files are excluded. Dataset bytes, prompts,
scripts and opaque experimental values are preserved. Known knowledge display
paths become relative archive directories; sampling snapshot keys identify
credential-free explicit input in the archive, rather than executable runtime
resolver requests. Importers must use this schema and restore connections;
the package is not a direct runtime-resume image.

Optional results include every retained production attempt for the selected
publication and design. They exclude test runs, node previews, other versions,
queues, workspace resume/control state, regenerable row views/indexes, caches
and scratch. UUID-isolated retained outputs include Agent slots and Script
working directories. Shared legacy workspaces and experiment-level artifacts
without publication attribution are not assigned to historical versions; the
dependency inventory records unavailable or unscoped evidence.
AgentRun IDs in node evidence are source provenance; native Motoro step history
and resume/control snapshots are not bundled.

Archive generation streams file bytes and SQL evidence slices, spools inventories
to plain files, and discards tar member metadata after writing. Memory does not
grow with the number of attempts, archive members or dataset rows. Publication
configuration and one bounded Skill/resource record are processed at a time.
Copying/compression run off the event loop; cancellation stops and joins active
writer work before cleanup. No new database or SQLite inventory is introduced.

A package is a logical export, not a database backup. Resuming workspaces and
reproducing external model/tool outputs are outside its scope. See
[ADR 0003](adr/0003-immutable-dataset-inputs-and-managed-artifacts.md).

## Import workflow

On **Experiments**, choose a `.tar.gz` package under **Import experiment
package**. Upload starts inspection only. Review the source version, schema,
file categories, missing dependencies, historical results and local configuration
requirements, then explicitly confirm creating a new experiment. The persistent
request list lets you return after navigation or a browser restart. Cancel before
activation or retry a failed unexpired request. A distinct request creates a
distinct owned experiment even for identical archive bytes.

The new experiment contains an editable draft and planned cells/replicates.
It has no selected local publication and no fabricated completed executions.
Imported source publications, resolver/sampling snapshots, all retained attempts,
observations and artifacts stay immutable in managed files. **Download experiment
package → Imported source evidence** exposes those files separately from local
Runs/Results projections. It also provides an exact source package re-export.
New local exports retain source manifests/publications and typed mappings under
`sources/`; including results additionally preserves the original source archive
as an opaque nested evidence file. Nested archives are never executed or used
as native runtime state.

Configure the draft's model credentials, MCP connections and unresolved
dependencies locally, acknowledge configuration, then use normal publication.
Acknowledgement checks owned dataset/Skill/server dependencies without connecting
to external services. Normal publication resolves new local sampling/input
snapshots; it does not rewrite the preserved source evidence. Resource names
receive an import-specific suffix to avoid collisions. Canvas node IDs remain
protocol-scoped labels because prompts may address them; new protocol ownership
creates a fresh identity namespace. Only typed resource references, selections
and bound factor alternatives are remapped. Opaque strings, scripts, prompts,
dataset values, observation values and provenance are not searched/replaced.

### API

All routes below require the importing user's authentication:

1. `POST /api/experiment-package-imports` with `{"request_key":"client-unique-key"}`.
   The same owner/key returns the same durable request.
2. `PUT /api/experiment-package-imports/{id}/upload` with raw gzip archive bytes.
   A disconnected upload can be repeated before inspection; no multipart buffering
   is required.
3. Poll `GET /api/experiment-package-imports/{id}` for status, progress, diagnostics,
   preflight, source SHA-256 and destination experiment ID.
4. `POST .../{id}/confirm` explicitly queues activation. Repeated confirmation is
   idempotent. `POST .../{id}/cancel` cancels before completed activation;
   `POST .../{id}/retry` re-inspects a failed request before another confirmation.
5. `GET .../{id}/files?offset=0&limit=50` pages source inventory;
   `GET .../{id}/source?path=publication/canvas.json` downloads one original file.
   Omitting `path` re-exports the original source archive byte-for-byte.
   `GET .../{id}/identities?offset=0&limit=50` pages durable typed provenance.
6. After configuring the editable draft, `POST .../{id}/configuration` with
   `{"connections_configured":true,"dependencies_resolved":true}` acknowledges
   local configuration. It does not publish or start execution.

`GET /api/experiment-package-imports?experiment_id={id}` finds an experiment's
source history; omitting the filter returns the newest 50 owned requests.

### Lifecycle, limits and recovery

The existing results worker relays durable `validating`, `queued` and `activating`
requests. PostgreSQL advisory locks serialize a request across workers. Archive
extraction, inventories and evidence use filesystem staging, not SQLite. Hashes
and references are verified before activation and the archive is re-inspected
before reconstruction. IDs derive from the request and typed source identity;
retry adopts only that request's owned staged resources. Motoro and ASAREE use
separate transactions. Skills stay soft-deleted and MCP registrations stay
disconnected/hidden during resource staging. Native experiment/protocol readers
hide incomplete activation. The relay finishes an interrupted visibility flip;
failures/cancellation compensate only records allocated by that request.

Uncompleted requests expire after 24 hours; cancellation schedules cleanup on the
next results-worker relay. Cleanup waits for the request's worker lock and removes
only its staging paths and resources. Completed imports are durable evidence and
do not inherit download-export expiry. Include their files and database records
in coordinated backups. The API and results worker must share the same managed
storage configuration.

Configurable environment limits (positive integers):

| Setting (`ASAREE_` prefix) | Default |
| --- | --- |
| `PACKAGE_IMPORT_MAX_COMPRESSED` | 1 GiB |
| `PACKAGE_IMPORT_MAX_EXPANDED` | 8 GiB |
| `PACKAGE_IMPORT_MAX_MEMBERS` | 100,000 files |
| `PACKAGE_IMPORT_MAX_DOCUMENT` | 16 MiB per JSON document, JSONL record or Skill resource |

Files stream in bounded blocks; attempt inventories are disk-backed. JSON canvas,
settings and one evidence document must fit the configured document limit.
Archives exceeding any limit fail inspection with a diagnostic; increase limits
deliberately for larger recorded evidence. Traversal, links, special/sparse files,
duplicate/conflicting members, invalid JSON, unsupported schemas, unlisted files,
incorrect hashes/sizes and inconsistent typed references are rejected. Archive
ownership fields never confer authorization. No packaged script/tool or server
connection is invoked by inspection or reconstruction.

### Export-to-import coverage audit

| Exported component | Local reconstruction / retained evidence |
| --- | --- |
| Manifest, inventories, source version and capture provenance | Immutable exact original files, source archive SHA-256, durable owned request |
| Canvas, Agent configs, model requirements, settings, measurements | Editable sanitized draft; source snapshots retained separately; local publication required |
| Design declarations, cells, planned replicates | New local IDs; typed references and resource-valued factor alternatives remapped |
| Dataset bindings, projections and ordering | Exact source binding evidence; graph wiring/order retained; new owned registrations |
| Original CSVs, pinned split files and split provenance | Exact filesystem bytes/hashes, owned artifact/split records, no row-index import |
| Scripts and inline content | Exact source content; no execution during import |
| Skills, discovery fields, frontmatter and all resource files | Owned Motoro Skills/files; UTF-8/base64 encoding and order from v2 metadata |
| Knowledge trees and MCP requirements | Owned managed copies and disconnected registrations; explicit local configuration |
| Optional attempts, observations, row slots, conversations, node evidence, usage/errors | Source-attributed historical files and identity mappings; no native runtime records or latest-result projection |
| Attributable durable output/artifact files | Exact source files with hashes and attempt attribution; re-exportable source evidence |
| Missing/excluded dependencies and unavailable history | Visible diagnostics and preserved inventories; no same-name substitution |

The PostgreSQL tests cover independent owners and both dataset modes, exact CSV
and binary Skill bytes, publication after acknowledgement, source re-export,
ownership isolation and cross-database crash/retry/cancel boundaries. Archive
tests cover corruption, unsafe members, limits, compatibility and non-execution.
Native Motoro step/resume histories were never exported and cannot be restored.
