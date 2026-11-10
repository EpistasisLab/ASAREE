# ASAREE agent instructions

ASAREE runs factorial experiments over agent protocol graphs. Use these shared
instructions with Codex and Claude Code; specialized rules live near their code.

## Read before changing code

- Read applicable nested `AGENTS.md` files from root to the area being edited, even when
  the CLI starts at the repository root. More specific rules refine parent rules;
  explicit user instructions take precedence. Nested files are instructions, not subagents.
- For frontend work, read [frontend instructions](frontend/AGENTS.md), then applicable
  [page](frontend/src/pages/AGENTS.md),
  [protocol](frontend/src/components/protocol/AGENTS.md), and
  [Cells](frontend/src/components/protocol/cells/AGENTS.md) instructions.
- For backend work, read [backend instructions](src/asaree/AGENTS.md).
- Before exploring or implementing domain behavior, read [CONTEXT.md](CONTEXT.md) and
  relevant available ADRs under `docs/adr/`. Use their canonical vocabulary and surface
  proposed contradictions. Some supporting docs are private/local; if absent, proceed
  without creating replacements speculatively.
- Before changing experiment datasets, agents, factors, revisions, execution/results, or
  measurement behavior, read [experiment contracts](docs/agents/experiment-contracts.md).
  This applies across backend, frontend, SDK, and related tests.

## Execution and cost

- Work solo by default. At most one scoped search agent is appropriate for a targeted
  investigation; use parallel agent fan-out only when the user explicitly requests it.
- Search the named area first; widen only when focused searches are insufficient.
- Run the narrowest relevant lint/type/test checks. Ask before long builds, checks taking
  minutes, expensive statistical simulations, or starting extra infrastructure.
- Ask before browser automation, Playwright, or screenshot-based UI verification. The
  user checks UI appearance; use type checks, lint, and existing tests for routine work.
  State any behavior that cannot be confirmed without a browser.
- When approval is needed, explain the action and its purpose in one sentence. Existing
  explicit authorization remains valid; do not ask again for an already authorized action.

## Verification

- Before pushing code, run the affected CI suites locally using
  [local CI verification](CONTRIBUTING.md#local-ci-verification) and fix failures
  locally first. Use GitHub CI to verify a locally passing change. If local
  verification is blocked, explain the missing prerequisite and get approval
  before substituting a GitHub CI run. Existing authorization still applies.
- Frontend: commands and working directory are in [frontend instructions](frontend/AGENTS.md).
- Backend and backend tests: read [backend verification](src/asaree/AGENTS.md#verification),
  including service prerequisites and expensive-test markers.
- For SDK, workspace-core, and MCP package work, inspect the affected package configuration
  and existing tests before selecting focused checks.
- For documentation-only edits, check local links, instruction scope, and `git diff --check`;
  application builds/tests are unnecessary unless executable behavior changed.
- Before changing CI or preparing integration/release work, read
  [CONTRIBUTING.md](CONTRIBUTING.md). Report checks run, failures, and unverified behavior.

## Git and releases

- Follow [CONTRIBUTING.md](CONTRIBUTING.md): features/fixes branch from current `develop`
  and target `develop`. Set the PR base explicitly; GitHub defaults to `main`.
- Preserve commit ancestry with merge commits, including feature, release, hotfix, and
  synchronization PRs; refresh branches by merging rather than rebasing.
- Explicit user authorization is required before merging into `main` (including hotfixes),
  creating release tags, publishing artifacts, or deploying production.
- Replace temporary Motoro integration pins with tested release tags before production
  releases, following the dependency validation in `CONTRIBUTING.md`.
- Do not add `Co-Authored-By` or `Generated-with` lines to commits or PRs.

## Issue tracker

- Issues/specs live in `EpistasisLab/ASAREE_Issues`; code and PRs live in `EpistasisLab/ASAREE`.
  Use `gh` and explicitly pass `-R EpistasisLab/ASAREE_Issues` for issue operations.
- Reference issues from PRs as `EpistasisLab/ASAREE_Issues#<number>`.
- Use canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`,
  `ready-for-human`, and `wontfix`.
- For detailed issue/triage workflows, read `docs/agents/issue-tracker.md` and
  `docs/agents/triage-labels.md` when present. They are optional local references;
  the shared tracker and label rules above remain authoritative when they are absent.

## Communication

- Be concise and execution-oriented. Make routine implementation decisions and carry out
  authorized work; ask only when ambiguity materially changes requirements or correctness.
- Explain significant decisions or concerns briefly, with one recommended course of action.
  Give alternatives or deeper explanations when requested or when the choice is consequential.
- Finish with a short account of changes, verification, and material limitations.
