# Experiment portability implementation

Historical implementation notes for the original package workflow. The current
product contract is [experiment packages](experiment-packages.md): publication
before export, configuration/input assets only, automatic draft creation, recreated
persona snapshots, and node-based repairs. The confirmation/setup and results
paths described below have been superseded.

Branch: `fix/package-export-publication-join`; starting HEAD: `f27437d`.
Preserve existing ancestry and leave PR #56 (base `develop`) unmerged.

1. Add stable built-in service identities and capability-based destination matching.
   Preserve destination connection settings; retain owned imported requirement IDs
   and record destination runtime bindings separately as provenance.
2. Add explicit authorized destination connection selection for custom MCP and
   model providers, including factor alternatives. Never execute packaged code,
   import credentials, or run live connection tests during setup validation.
3. Replace acknowledgement with read-only readiness checks in publication;
   surface node/resource issues and allow incomplete drafts to remain editable.
4. Show readiness and setup actions in the imported experiment and link completed
   imports directly to that draft. Preserve source inspection and existing UI work.
5. Separate export contents, destination requirements, and missing content;
   preserve historical fidelity and the publish-before-download option.
6. Add focused cross-installation coverage for connection matching, fresh owned
   identities, factor alternatives, exact bytes, evidence, retry and readiness.

Verification: focused Python unit tests/lint and frontend tests/type checking.
Database integration uses only explicitly configured disposable PostgreSQL, never
application databases or application `.env` files. Obtain approval before starting
infrastructure, checks taking minutes, or browser automation.

Identity clarification for #70: imported content always receives fresh owned IDs.
Runtime bindings may reference authorized destination system services. Source IDs
and source-to-local mappings remain immutable provenance; destination commands,
environment and credentials are never replaced by archive data.

Implementation status: all six steps implemented. Readiness and destination
selection are available on the imported canvas; the legacy acknowledgement API
is retained only for compatibility. New schema-2 requirements carry stable
service identities and required-tool subsets. Legacy requirements require
explicit destination selection. Source provenance scopes reselection when
several requirements share one destination connection.

Verification (2026-11-11): disposable PostgreSQL 16.2, isolated source/destination
product/resource schemas and filesystem roots; full backend suite passed
2,137 tests (3 skipped; 14 statistical-validation tests deselected). Full frontend
suite passed 664 tests with two workers; default concurrency exposed an existing
metrics-autosave timing failure, which passed alone. Backend lint and frontend
type checks passed. Full mypy has 695 existing errors versus 697 at the starting
HEAD, with no new diagnostics; the four portability service modules pass focused
typing. The first full backend attempt had a launcher-relative scripts-path
failure, fixed in the disposable launcher before the successful full rerun.
Final focused checks passed 121 backend tests and 19 frontend tests, additionally
covering later provenance/reselection changes. `git diff --check` and local
documentation links passed. Implementation is recorded in the branch commit.

No application databases or application `.env` files were used. Browser
appearance and live provider/MCP behavior were not tested. All original commits
and the two previously unpushed commits remain; no push or merge was performed.
