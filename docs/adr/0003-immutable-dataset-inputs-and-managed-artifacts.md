---
status: accepted
date: 2026-11-09
---

# Preserve immutable dataset inputs with PostgreSQL records and managed files

ASAREE must preserve experimental inputs across re-splitting, publication,
execution, storage relocation, and experiment downloads. Keep original CSVs,
exact partition files, and derived datasets in managed filesystem storage;
store their identities, relationships, provenance, and derived row indexes in
the existing ASAREE PostgreSQL database. Motoro retains its existing database;
this work introduces no additional database technology.

This is the accepted target design, not a claim that the behavior is already
implemented. Implementation is divided between
[immutable splits (#67)](https://github.com/EpistasisLab/ASAREE_Issues/issues/67),
[pinned inputs (#68)](https://github.com/EpistasisLab/ASAREE_Issues/issues/68),
[managed storage (#50)](https://github.com/EpistasisLab/ASAREE_Issues/issues/50),
[PostgreSQL row indexes (#69)](https://github.com/EpistasisLab/ASAREE_Issues/issues/69),
and [experiment packages (#55)](https://github.com/EpistasisLab/ASAREE_Issues/issues/55).

## Dataset source identity

The existing registered dataset UUID identifies one immutable original source;
its SHA-256 verifies the original bytes. A separate source table is not required
solely to introduce another identifier. Replacing the original data creates a
new registration. Names and descriptions may change without changing source
identity, and ownership remains attached to the registration.

Whole-dataset and per-row execution refer to the same source. The derived row
index preserves original row positions, column order, and exact string values.
Its parsing rules are recorded separately: changing a parser does not change
the original source. Build this index when row-oriented functionality requires
it, using bounded-memory ingestion and batched PostgreSQL reads, rather than
requiring every whole-dataset upload to become database rows.

## Immutable splits and current selection

Each successful split operation creates a split UUID associated with its
dataset. A split records train/test artifact references and hashes, creation
time, effective target column, and available provenance. Quick splits record
the actual strategy, seed, requested test fraction, and grouping/stratification
behavior. Manual splits record their supplied origin without inventing their
generation procedure or claiming verified source-row membership.

The dataset has a nullable `current_split_id`. Creating a successful split
makes it current; selecting an older split changes only that pointer. Neither
operation modifies another split's files or provenance. Registration without
a split remains supported. Compatibility response fields such as `train_path`
are resolved from the selected split, not maintained as another authoritative
selection. Publish the new split and update the selection only after successful
artifact preparation, preserving the previous selection on failure.

## Managed artifact references

A managed-artifact record in ASAREE PostgreSQL has an artifact UUID, storage
namespace and relative storage key, content hash, byte size, format, and
preparation/availability status. Resolve deployment-specific absolute paths
when files are needed; durable references use stable artifact identities.
For example, a split references a train artifact whose storage key may be
`datasets/<dataset-id>/splits/<split-id>/train.parquet`. The precise directory
layout remains an implementation decision.

Original CSVs and exact partition files are authoritative file artifacts. The
row index is derived and is not independently editable. Artifact availability
may change without changing content identity; relocation must preserve bytes
and references. Referenced artifacts are protected from cleanup. Identical
hashes do not imply permission to share content across owners; automatic global
deduplication is not required.

Issue #67 introduces only the artifact handling its sources and splits need.
Issue #50 extends the same contract to other managed files and migrates existing
path references. Avoid implementing a general storage framework upfront.

## Publication and attempt inputs

A published experiment version freezes dataset sources and split selections
required by its graph and factor alternatives. Publication resolves a current
split selection to a specific split UUID. Each attempt records the resulting
inputs for its cell and Agent bindings. Preserve binding order and ownership
checks.

For example: publishing with split A pins A. Creating split B changes the
dataset's default, but executing or resuming the older publication still uses
A. A newly published draft may select B. An unsplit binding explicitly selects
the original source: creating a split later must not change that binding.
Per-row inputs retain their original row positions and projections.

Workspace reuse validates pinned input identities. Identical bytes do not
automatically authorize sharing workspaces across publications or attempts.
Issue #68 settles the detailed sharing rules; #67 supplies the identities.
Missing or mismatched pinned inputs must not fall back to the current selection.

## Migration and historical evidence

Verify available legacy files and metadata and represent them as initial
source/split/artifact records without regenerating or changing their bytes.
When a missing hash is computed during migration, it establishes the observed
file's identity at that time, not proof of an old attempt's input. Unknown
provenance remains unknown. Report missing files and hash mismatches explicitly.
Do not assign the current split to an old publication as though its historical
selection were known, and do not reconstruct overwritten history speculatively.

A manual split's association with a registered dataset expresses the user's
association; it does not prove that the supplied rows partition the original
CSV. Preserve the supplied artifacts regardless of whether source-row membership
can be established.

## Consequences and alternatives

Keeping files supports existing scripts, ML tools, exact-byte preservation,
and experiment archives. It requires coordinated backup and lifecycle handling
for PostgreSQL records and managed files. Stable references and integrity checks
address that coordination; relocating a root alone is not a migration.

Storing all parsed datasets in PostgreSQL would still require materialization
for existing file-based tools and would not preserve original CSV bytes.
Storing all file bytes as database blobs would enlarge database backup/restore
workloads while still requiring runtime extraction. Neither is required for a
portable download. PostgreSQL replaces SQLite specifically for indexed row
access, with no per-row network-query port of the synchronous SQLite interface.

Experiment packages resolve pinned dependencies into a versioned archive with
relative references and verified hashes. They omit regenerable row indexes,
caches, and scratch. Packages are logical exports, not database backups, and
exact inputs do not promise identical external-model outputs.

## Implementation handoff

Implement #67 first, then #68, #50, #69, and #55 in separately reviewed changes.
Coordinate shared identities and references before implementation; do not
implement follow-up features as incidental scope in #67. Its split history
alone does not make historical execution reproducible before #68 is complete.

Implementation sessions must read this decision alongside
[the glossary](../../CONTEXT.md) and
[the experiment contracts](../agents/experiment-contracts.md). Table layouts,
exact directory hierarchy, detailed retention, and archive schema remain scoped
to their implementation issues. This decision extends the existing publication
contract without adding another experiment-version counter.
