#!/usr/bin/env bash

set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
task_dir="$repo_root/tasks/asaree-row"
start_number="${1:-005}"

if [[ ! -f "$repo_root/.env" ]]; then
  printf 'Missing environment file: %s\n' "$repo_root/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$repo_root/.env"
set +a

# Motoro and ASAREE use different names for the same core database URL.
if [[ -n "${ASAREE_DATABASE_URL:-}" ]]; then
  export DATABASE_URL="$ASAREE_DATABASE_URL"
fi

if [[ ! "$start_number" =~ ^[0-9]+$ ]]; then
  printf 'Usage: %s [starting task number]\n' "${0##*/}" >&2
  printf 'Example: %s 005\n' "${0##*/}" >&2
  exit 2
fi

start_number=$((10#$start_number))
found=0

shopt -s nullglob
tasks=("$task_dir"/ASAREE-ROW-L*.yaml)

cd -- "$repo_root"

for task in "${tasks[@]}"; do
  task_name="${task##*/}"
  task_number="${task_name#ASAREE-ROW-L}"
  task_number="${task_number%.yaml}"

  [[ "$task_number" =~ ^[0-9]+$ ]] || continue
  ((10#$task_number >= start_number)) || continue

  found=1
  printf '\n==> Implementing %s\n' "$task_name"

  if swe implement "$task"; then
    printf '==> Completed %s\n' "$task_name"
  else
    status=$?
    printf '==> Stopping: %s failed (exit %d)\n' "$task_name" "$status" >&2
    exit "$status"
  fi
done

if ((found == 0)); then
  printf 'No ASAREE row tasks found at or after L%03d in %s\n' \
    "$start_number" "$task_dir" >&2
  exit 1
fi

printf '\nAll selected ASAREE row tasks passed.\n'
