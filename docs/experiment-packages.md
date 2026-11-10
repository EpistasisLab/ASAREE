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

## Schema version 1

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

A package is a logical export, not a database backup. Importing packages,
resuming workspaces, and reproducing external model/tool outputs are outside its
scope. See [ADR 0003](adr/0003-immutable-dataset-inputs-and-managed-artifacts.md).
