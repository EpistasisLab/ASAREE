# Development and releases

`main` is the default branch and holds released code. `develop` is the integration branch.
Use tagged releases for reproducible installations and production deployments.

| Work | Branch from | PR target | Merge method |
| --- | --- | --- | --- |
| Feature (`feat/*`) or fix (`fix/*`) | `develop` | `develop` | Merge commit |
| Release promotion | `develop` | `main` | Merge commit |
| Urgent released-code fix (`hotfix/*`) | `main` | `main` | Merge commit |
| Synchronize a release or hotfix | `main` | `develop` | Merge commit |

Merging into `main` (including hotfixes), creating release tags, publishing
artifacts, and deploying production require explicit maintainer authorization.
Successful CI or completion of integration work alone does not authorize these
release operations.

Create a feature branch from current `origin/develop`, keep changes on that
branch, and open a draft PR targeting `develop`. Complete focused local checks,
then wait for GitHub CI, resolve review conversations, mark the PR ready, and
merge using a merge commit. Verify CI on the resulting `develop` commit as well
as the PR. Preserve the feature branch's original commits; do not squash or
rebase-merge PRs.

GitHub defaults new PRs to `main`; explicitly select `develop` for feature/fix
PRs. With gh, use `gh pr create --base develop --draft`. The required backend CI
check rejects `feat/*` and `fix/*` PRs targeting `main`; release PRs from `develop`
and hotfix PRs can target `main`.

Both long-lived branches require PRs, resolved review conversations, and these
GitHub Actions checks on an up-to-date branch:

- `Lint & Test (backend)`
- `Type Check & Lint (frontend)`

Reviewer approvals are set to zero for solo maintenance. Force pushes and branch
deletion are blocked, and there are no bypass actors. GitHub currently permits
both squash and merge commits into `develop`, but this workflow uses merge
commits to preserve commit ancestry. `main` permits merge commits only. Linear
history is not required because releases and synchronization must preserve merge ancestry.
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

The provider-operations integration was first tested against Motoro commit
`227311f683e6be629ac4e7b2cc93a1ed1323d084`
([EpistasisLab/motoro#15](https://github.com/EpistasisLab/motoro/pull/15)). Motoro
subsequently released those changes in `v0.14.0`. The current provider-driven
sampling integration pins Motoro develop commit
`52dfd0720d9d10850c1e3099f4149b3c392192b9` pending a tested release tag. For future
integrations, **release Motoro first** and complete this sequence before ASAREE's
production release:

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

## Provider-driven sampling migration

Discovered `model_metadata` retains provider provenance. `resolved_capabilities`
is reserved for deliberate caller overrides. The model-list and custom-capability
responses keep their flat capability fields for compatibility and add the full
`model_metadata`; previews send that full metadata instead of treating discovery
as an override. Unknown support permits explicit input; unset, unadvertised
defaults remain omitted.

Redis model-list namespaces use v6, invalidating old fresh, retained, and recent
results. Frontend model-list, capability, and sampling query keys are versioned
too. No cache deletion or database backfill is needed.

Saved draft capability rules with no recorded origin require an explicit choice
in the Model inspector before publication: refresh Automatic metadata or keep
them as a deliberate override. This also applies to whole-model factor levels.
Provider discovery copies refresh when a draft executes or publishes.

New sampling snapshots use schema version 2 and record explicit input, discovered
facts, overrides, sources, omission reasons, provider request sampling fields,
connection identity, discovery time (unknown when unavailable), and resolver and
Motoro versions. Published execution never rediscovers metadata. Unadvertised,
omitted defaults remain provider-controlled and cannot promise exact replay.

Existing publications are immutable. Snapshots from the older resolver require
their original Motoro runtime for replay; this integration refuses to silently
reinterpret them. Republish the editable draft to opt into the new policy.

## CI and release safety

CI runs on pushes to `develop` and `main`, and when a PR targeting either branch
opens, reopens, or receives commits. Title, description, label, and review edits
do not trigger runs. A small dispatcher selects the relevant jobs from the full
Git diff; the rules live in `.github/ci/scope.py`.

| Changed files | Suites |
| --- | --- |
| Documentation, standard README/agent instructions, license, PR/issue templates, CODEOWNERS | Neither |
| `frontend/` code, assets, configuration, dependencies | Frontend |
| `src/`, `tests/`, `sdk/`, `workspace-core/`, `mcp-servers/`, `scripts/`, `personas/`, Python manifests/locks | Backend |
| Workflow/dispatcher code, shared build/container configuration, or unrecognized paths | Both |

Documentation exceptions apply before directory rules. Empty diffs skip both
suites. Mixed changes select every affected suite; deletions and both sides of
renames count. PRs use the full diff from their merge base; pushes include every
commit since the previous branch tip. Unknown comparison commits run both suites.

The dispatcher runs dependency-free tests even for docs-only changes. Skipped
jobs still complete the existing required checks, without installing dependencies
or starting PostgreSQL. Do not add workflow-level path filters or commit-message
skip directives: missing required checks can block a docs-only PR. A failed
dispatcher blocks merging through the required backend check. The feature/fix PR
target guard runs even for docs-only changes.

Backend CI checks the lockfile, lints, migrates disposable PostgreSQL databases,
and runs the full test suite. Frontend CI type-checks, runs unit tests, and lints.
Integration checks on `develop` and `main` remain enabled and use the same scope
rules; release PRs retain checks against their target branch.

The full backend mypy step is currently warn-only while its existing backlog is
resolved; oxlint warnings also do not fail frontend CI. Report warnings separately
from passing required checks.

At workflow setup, this repository has only the CI workflow and no GitHub
deployment environments or webhooks. CI does not deploy, create release tags, or
publish artifacts. Inspect triggers again before future workflow or default-branch
changes. Local Docker Compose commands explicitly build/start services and run
migrations; they are not part of branch promotion.
