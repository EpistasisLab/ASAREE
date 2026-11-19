# Experiment packages

The canvas **Download** action exports the published experiment and all required
input assets as a `.tar.gz` package. Publish the current draft before exporting;
Download does not publish it or offer a publish-and-export shortcut. When browsing
a historical version, Download exports that selected version.

Packages contain the canvas, experiment settings, measurement plan, design,
datasets and exact split files, scripts, Skills and their bundled files, knowledge
content, and saved persona snapshots as Markdown. Factor alternatives and
sub-agent definitions/wiring are included. Missing, changed, unreadable or
unauthorized required content fails preparation with a diagnostic; an incomplete
archive is never published as a download. Saved destination connections are
requirements rather than bundled assets. Credentials are excluded.

Results, observations, execution history, execution output artifacts, queues,
workspace resume state, row indexes, caches and scratch are excluded. Package
import does not restore results. Existing Results CSV exports are separate.

## Create experiment from file

Use **Create experiment from file** from the existing header menu. It accepts:

- Legacy `.json` experiment definitions, which have no asset file contents.
- `.tar.gz` packages containing the complete definition and input assets.

After choosing the file and creating the experiment, package inspection and
reconstruction proceed automatically. There is no preflight acceptance or
connection-acknowledgement step. Progress and failures appear in the same dialog;
failed requests can be retried. Completion opens the new experiment's canvas.
Both formats create an **unpublished editable draft**. A JSON file's source
published snapshot does not create a destination publication.

Saved provider connections resolve through the destination user's ordinary
provider settings. Trusted built-in MCP registrations match only when their
stable service identity and required tools agree and the match is unique. Custom
MCP connections are never selected automatically. Missing or ambiguous connections
appear as node warnings; choose a compatible saved connection in the affected
node inspector. Missing credentials are repaired in Profile. Normal discovery
and validation handle connectivity; import never starts packaged commands,
executes scripts, invokes tools, tests credentials or runs an experiment.

## Destination identities and storage

The imported experiment, protocol and owned resources receive fresh destination
identities. Typed references are remapped, including factor alternatives,
measurement bindings, and ordered dataset/Skill/knowledge selections. Node IDs
remain scoped to the new protocol so edges, sub-agent selections and prompt
references continue to agree. Prompts, scripts and data are opaque content;
hard-coded external addresses or paths inside them are not rewritten.

Dataset sources and exact split bytes are retained in managed storage. Knowledge
registrations point to destination managed directories. Each distinct saved
persona snapshot becomes a reusable destination Persona; a node combining
multiple personas recreates its exact combined prompt as one entry. Source
persona IDs do not become live destination references. Changing a library persona
does not change an imported node's saved text.

Normal experiment creation and protocol naming are reused. Experiment names keep
the original when available, otherwise use the existing `Name (2)` convention.
Resource names follow their owning namespace's uniqueness and validation rules;
there is no unconditional import-UUID suffix. Source manifests and typed identity
mappings retain offline provenance without requiring access to the source
installation.

## Format and integrity

Packages use `asaree.experiment-package`, schema 2. Payloads contain:

| Path | Contents |
| --- | --- |
| `manifest.json` | Publication identity, capture information, software requirements and inventory hashes |
| `files.jsonl`, `dependencies.jsonl` | Member hashes, sizes, dependency identities and connection requirements |
| `publication/`, `experiment-metadata.json` | Canvas and experiment configuration |
| `design/` | Generated design and pending replicate slots, without results |
| `inputs/` | Pinned datasets, exact originals/splits and provenance |
| `resources/` | Scripts, Skills, knowledge and persona Markdown snapshots |
| `requirements/` | Credential-free provider and MCP requirements |
| `sources/` | Imported source configuration and identity provenance |

Published configuration and dataset input identities are pinned. Reusable Skills
and knowledge are captured at download time; the manifest records that distinction.
ASAREE and Motoro have separate database snapshots, and mutable files have no
historical transaction guarantee. Packages are logical exports, not database
backups or promises of identical external-model outputs.

Archive inspection verifies hashes, identity relationships and file counts before
creating records. It enforces compressed/expanded/member/document limits and
rejects traversal paths, links, special files and excluded credential-file names.

## External knowledge export grants

Uploaded, owned knowledge is exportable. Registering an external path or allowing
it to be browsed does not authorize packaging its contents. Operators can grant a
user access to one exact external bundle root with `knowledge_export_grants`
(`ASAREE_KNOWLEDGE_EXPORT_GRANTS`), a JSON mapping of user UUIDs to absolute paths.
Grants do not authorize ancestors/descendants, broad roots, platform-storage
overlaps, or another owner's overlapping registrations. Configure the same grants
and mounted paths on the API and results worker. Ambiguous authorization fails
export without including foreign content.

## API

Create an owned export job with
`POST /api/experiments/{experiment_id}/execution-results/jobs`, supplying
`protocol_id` and `protocol_revision_id` query parameters and a body such as
`{"kind":"export","format":"experiment_package","request_key":"unique-key"}`.
Results requests are rejected. Poll the existing job endpoint for its native
download URL. Signed links last five minutes and refresh on status reads;
prepared downloads expire after 24 hours.

Create an import with `POST /api/experiment-package-imports` and
`{"request_key":"unique-key"}`, upload raw gzip bytes through
`PUT /api/experiment-package-imports/{id}/upload`, then poll
`GET /api/experiment-package-imports/{id}`. Validation queues reconstruction
automatically. The owned `cancel` and `retry` actions remain available. The
`readiness` and `connections` routes support canvas warnings and inspector repairs;
they do not constitute a separate setup workflow. Source-file and identity readers
remain authenticated diagnostic interfaces.

## Lifecycle, limits and recovery

The existing results worker relays durable `validating`, `queued` and `activating`
requests. Advisory locks serialize work on a request. Deterministic identities
within that request permit recovery without duplicating resources; separate
requests create separate experiments. Content is staged before visibility changes,
and incomplete resources remain hidden. Cancellation/failure cleanup removes only
that request's staged resources. Uncompleted requests expire after 24 hours.
Completed input assets and source provenance remain available in managed storage.

See [managed storage](managed-storage.md) and
[ADR 0003](adr/0003-immutable-dataset-inputs-and-managed-artifacts.md).
