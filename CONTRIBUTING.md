# Development and releases

`develop` is the default and integration branch. `main` holds released code.
Use tagged releases for reproducible installations and production deployments.

| Work | Branch from | PR target | Merge method |
| --- | --- | --- | --- |
| Feature (`feat/*`) or fix (`fix/*`) | `develop` | `develop` | Squash normally |
| Release promotion | `develop` | `main` | Merge commit |
| Urgent released-code fix (`hotfix/*`) | `main` | `main` | Merge commit |
| Synchronize a release or hotfix | `main` | `develop` | Merge commit |

Create a feature branch from current `origin/develop`, keep changes on that
branch, and open a draft PR targeting `develop`. Complete focused local checks,
then wait for GitHub CI, resolve review conversations, mark the PR ready, and
squash-merge. Verify CI on the resulting `develop` commit as well as the PR.

Both long-lived branches require PRs, resolved review conversations, and these
GitHub Actions checks on an up-to-date branch:

- `Lint & Test (backend)`
- `Type Check & Lint (frontend)`

Reviewer approvals are set to zero for solo maintenance. Force pushes and branch
deletion are blocked, and there are no bypass actors. `develop` permits squash
and merge commits; `main` permits merge commits only. Linear history is not
required because releases and synchronization must preserve merge ancestry.
Refresh a behind feature branch by merging `origin/develop` into it, then rerun
CI. Never weaken protections to complete a merge.

## Motoro integration

ASAREE consumes Motoro in-process through `pyproject.toml`'s uv Git source and
`uv.lock`. Stable production releases use a tested Motoro release tag.

A feature may temporarily pin an exact Motoro `develop` commit while its changes
are awaiting release. Regenerate the lockfile with uv and validate against that
installed revision, including provider discovery, connection checks, metadata
and defaults, credential isolation, and inference integration. Run full CI on
the PR and the resulting integration commit. Commit pins are integration inputs,
not a production release dependency policy.

For the provider-operations integration, Motoro PR #15 merged as
`227311f683e6be629ac4e7b2cc93a1ed1323d084`. That commit is the temporary integration
pin. **Motoro must be released first.** Before ASAREE's production release:

1. Replace the integration pin with the tested Motoro release tag. Update the
   installed-revision regression check to verify the tag's source provenance.
2. Regenerate `uv.lock` with uv, install frozen dependencies, and rerun integration
   validation and GitHub CI.
3. Review the complete `develop` versus `main` diff, including other integrated
   features and any database migrations.
4. Prepare the release PR from `develop` into `main` after integration validation.
   Merge it deliberately using a merge commit once its checks pass.
5. Create the ASAREE release tag, publish artifacts, and deploy only as separately
   authorized release operations. Synchronize `main` back into `develop` through
   a merge-commit PR.

For a hotfix, branch from current `main`, validate and merge its PR into `main`,
then open `main` → `develop` and merge with a merge commit. Keep any release tag
or deployment a deliberate operation.

## CI and release safety

CI runs on pushes to `develop` and `main`, and on PRs targeting either branch.
Backend CI checks the lockfile, lints, migrates disposable PostgreSQL databases,
and runs the full test suite. Frontend CI type-checks, runs unit tests, and lints.

The full backend mypy step is currently warn-only while its existing backlog is
resolved; oxlint warnings also do not fail frontend CI. Report warnings separately
from passing required checks.

At workflow setup, this repository has only the CI workflow and no GitHub
deployment environments or webhooks. CI does not deploy, create release tags, or
publish artifacts. Inspect triggers again before future workflow or default-branch
changes. Local Docker Compose commands explicitly build/start services and run
migrations; they are not part of branch promotion.
