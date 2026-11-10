"""Failure reporting through the executor, without database or provider calls."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from asaree.services import protocol_execution as pe
from asaree.services.protocol_failures import MAX_RUN_ERROR_LENGTH, summarize_node_failures


@pytest.mark.parametrize('kind', ['tool_step', 'agent', 'critic_gate'])
async def test_failed_execution_preserves_node_label_and_underlying_error(monkeypatch, kind):
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id, protocol_id=uuid.uuid4(), owner_id=uuid.uuid4(),
        status='pending', row_result_id=None, replicate_result_id=None, dataset_row=None,
        attempt_result=None, dataset_inputs=None, cancel_requested_at=None, protocol_revision_id=None,
        design_revision_id=None, replicate_label=None, factor_values={}, target_node_id=None,
    )
    node = {'id': 'score', 'type': 'tool_step' if kind == 'tool_step' else 'agent',
            'data': {'label': 'Score model', 'config': {}}}
    graph = {'nodes': [node], 'edges': []}
    gate = {'id': 'review', 'type': 'critic_gate', 'data': {'label': 'Review model', 'config': {}}}
    if kind == 'critic_gate':
        graph['nodes'].append(gate)
        monkeypatch.setattr(pe, 'find_gated_pairs', lambda *_args: {'score': gate})
    protocol = SimpleNamespace(id=run.protocol_id, experiment_id=None, graph=graph)
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    db.execute.return_value = result

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(pe, 'get_session', session)
    monkeypatch.setattr(pe, 'get_protocol_run', AsyncMock(return_value=run))
    monkeypatch.setattr(pe, 'get_protocol', AsyncMock(return_value=protocol))
    monkeypatch.setattr(pe, 'hydrate_registry', AsyncMock())
    monkeypatch.setattr(pe, 'validate_coordination_strategy', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pe, 'topological_order', lambda *_args, **_kwargs: [node])
    monkeypatch.setattr(pe, 'update_node_run', AsyncMock())
    status = AsyncMock()
    monkeypatch.setattr(pe, 'set_status', status)
    monkeypatch.setattr(pe, 'execute_tool_step', AsyncMock(return_value={
        'status': 'failed', 'error': 'Workspace not initialized: run the data-cleaning stage first.',
    }))
    monkeypatch.setattr(pe, '_node_run_context', AsyncMock(return_value=(
        {}, SimpleNamespace(seeded=[], unsplit_name=None),
    )))
    monkeypatch.setattr(pe, '_run_agent_node', AsyncMock(return_value=(
        None, 'Provider request timed out.', None, None,
    )))
    monkeypatch.setattr(pe, '_run_gated_worker', AsyncMock(return_value=(
        {'status': 'completed', 'error': None}, {'status': 'failed', 'error': 'Review provider quota exceeded.'},
    )))

    await pe.run_protocol(run_id)

    terminal = status.call_args.kwargs
    assert terminal['status'] == 'failed'
    assert ('Review model' if kind == 'critic_gate' else 'Score model') in terminal['error']
    assert {
        'tool_step': 'Workspace not initialized: run the data-cleaning stage first.',
        'agent': 'Provider request timed out.',
        'critic_gate': 'Review provider quota exceeded.',
    }[kind] in terminal['error']


def test_failure_summary_lists_actual_failures_and_does_not_blame_skipped_nodes():
    graph = {'nodes': [{'id': 'a', 'data': {'label': 'Writer'}}, {'id': 'b', 'data': {'label': 'Reviewer'}}]}
    summary = summarize_node_failures(graph, {
        'a': {'status': 'failed', 'error': 'Model is not available.'},
        'b': {'status': 'failed', 'error': 'Review timed out.'},
        'downstream': {'status': 'skipped'},
    })
    assert summary == 'Writer failed: Model is not available.\nReviewer failed: Review timed out.'
    assert 'downstream' not in summary


def test_failure_summary_handles_missing_details_and_preserves_specific_fallbacks():
    assert 'No underlying error was recorded' in summarize_node_failures({}, {'node-id': {'status': 'failed'}})
    assert 'no node-level error was recorded' in summarize_node_failures({}, {}, fallback='One or more nodes failed.')
    specific = 'Conversation depth limit reached.'
    assert summarize_node_failures({}, {}, fallback=specific) == specific


def test_failure_summary_bounds_large_errors_and_explains_omitted_nodes():
    summary = summarize_node_failures({}, {
        f'node-{index}': {'status': 'failed', 'error': 'x' * 10_000} for index in range(10)
    })
    assert len(summary) <= MAX_RUN_ERROR_LENGTH
    assert '5 additional nodes failed' in summary
    assert 'open execution details for the full error' in summary
