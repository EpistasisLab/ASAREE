"""User-facing run failures derived from the nodes that actually failed."""

from collections.abc import Mapping
from typing import Any

MAX_FAILED_NODES = 5
MAX_NODE_ERROR_LENGTH = 512
MAX_RUN_ERROR_LENGTH = 4096


def is_generic_run_error(error: str | None) -> bool:
    return not error or error.strip().lower().rstrip('.') == 'one or more nodes failed'


def summarize_node_failures(
    graph: Mapping[str, Any], node_runs: Mapping[str, Any], *, fallback: str | None = None,
    total_failures: int | None = None,
) -> str:
    labels = {str(node.get('id')): str((node.get('data') or {}).get('label') or node.get('id'))
              for node in graph.get('nodes') or []}
    failures = [(identifier, state) for identifier, state in node_runs.items()
                if isinstance(state, Mapping) and state.get('status') in {'failed', 'limit_reached'}]
    if not failures:
        return fallback if not is_generic_run_error(fallback) else (
            'The run failed, but no node-level error was recorded. Open the execution details to inspect its timeline.'
        )
    lines = []
    for identifier, state in failures[:MAX_FAILED_NODES]:
        label = labels.get(identifier, identifier)[:160]
        error = state.get('error')
        if isinstance(error, str) and error.strip():
            detail = error.strip()
            if len(detail) > MAX_NODE_ERROR_LENGTH:
                detail = detail[:MAX_NODE_ERROR_LENGTH] + '… (open execution details for the full error)'
        else:
            detail = ('The execution limit was reached.' if state.get('status') == 'limit_reached'
                      else 'No underlying error was recorded. Open the execution details to inspect this node.')
        lines.append(f'{label} failed: {detail}')
    total = total_failures if total_failures is not None else len(failures)
    if total > MAX_FAILED_NODES:
        lines.append(f'{total - MAX_FAILED_NODES} additional nodes failed. Open execution details for all errors.')
    return '\n'.join(lines)[:MAX_RUN_ERROR_LENGTH]
