"""Read-only draft row preview uses the same authorized runtime assembler."""
import hashlib
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from asaree.api import protocols as api
from asaree.deps import get_current_user
from asaree.models.database import get_db
from asaree.services import protocol_execution as execution
from asaree.services.dataset_row_csv import DatasetRowCsvError, project_row, read_row_source
from asaree.services.dataset_row_inputs import DatasetRowInputError


@pytest.fixture
def draft(tmp_path, monkeypatch):
    source = tmp_path / 'original.csv'
    source.write_text('question,reference\nfirst,GOLD-1\nsecond,GOLD-2\nthird,GOLD-3\n')
    registration = SimpleNamespace(id=uuid.uuid4(), raw_path=str(source),
                                   raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    graph = {'nodes': [
        {'id': 'dataset', 'type': 'dataset', 'data': {'config': {'dataset_id': str(registration.id)}}},
        {'id': 'agent', 'type': 'agent', 'data': {'config': {'prompt': 'Predict.'}}},
    ], 'edges': [{'id': 'edge', 'source': 'dataset', 'target': 'agent', 'targetHandle': 'dataset',
                 'data': {'dataset_input': {'mode': 'per_row', 'columns': ['question']}}}]}
    class Session:
        async def execute(self, statement):
            params = statement.compile().params
            assert registration.id in params.values()
            return SimpleNamespace(one_or_none=lambda: registration)
    @asynccontextmanager
    async def session():
        yield Session()
    monkeypatch.setattr(execution, 'get_session', session)
    async def forbidden(*args, **kwargs):
        pytest.fail('preview must not execute, seed, enqueue or fetch full metadata')
    for name in ('execute_run', 'fetch_owned_registration', 'create_protocol_run', 'seed_cell_workspace'):
        monkeypatch.setattr(execution, name, forbidden)
    return graph, registration, source


@pytest.mark.parametrize('index', [None, 2])
async def test_default_and_selected_preview_equal_runtime_without_source_leak(draft, index):
    graph, registration, source = draft
    snapshots = []
    text = await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4(),
                                              row_index=index, dataset_row_out=snapshots)
    original = read_row_source(dataset_id=str(registration.id), raw_path=str(source),
                               raw_sha256=registration.raw_sha256)
    view = project_row(original, row_index=index or 0, columns=['question'])
    assert snapshots == [view]
    assert text == execution._build_user_input(graph['nodes'][1], graph, {}, row_input_context=view)
    assert 'GOLD' not in text and 'reference' not in text
    assert ('third' if index else 'first') in text
    assert list(source.parent.iterdir()) == [source]


async def test_grader_explicit_columns_and_unwired_node(draft):
    graph, _, _ = draft
    graph['edges'][0]['data']['dataset_input']['columns'].append('reference')
    text = await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4(), row_index=2)
    assert 'GOLD-3' in text and 'GOLD-1' not in text
    graph['edges'] = []
    assert await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4()) == 'Predict.'
    with pytest.raises(execution.ProtocolValidationError, match='no_row_driver'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4(), row_index=2)


@pytest.mark.parametrize('index', [-1, 3, True, 1.5])
async def test_invalid_bounds(draft, index):
    graph, _, _ = draft
    with pytest.raises(DatasetRowCsvError):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4(), row_index=index)


async def test_invalid_columns_hash_empty_and_topology(draft):
    graph, reg, source = draft
    graph['edges'][0]['data']['dataset_input']['columns'] = ['missing']
    with pytest.raises(DatasetRowCsvError, match='invalid_columns'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4())
    graph['edges'][0]['data']['dataset_input']['columns'] = ['question']
    source.write_text('question,reference\n')
    with pytest.raises(DatasetRowCsvError, match='source_hash_mismatch'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4())
    reg.raw_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(DatasetRowCsvError, match='empty_source'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4())
    graph['nodes'].append({'id': 'other', 'type': 'dataset', 'data': {'config': {'dataset_id': str(uuid.uuid4())}}})
    edge = deepcopy(graph['edges'][0])
    edge.update(id='other-edge', source='other')
    graph['edges'].append(edge)
    with pytest.raises(DatasetRowInputError, match='multiple_drivers'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4())
    graph['nodes'][-1]['data']['config']['dataset_id'] = str(reg.id)
    graph['edges'][-1]['data'] = {}
    with pytest.raises(DatasetRowInputError, match='mixed_driver_modes'):
        await execution.preview_node_prompt(graph, 'agent', owner_id=uuid.uuid4())


async def test_route_uses_supplied_draft_or_stored_draft_and_returns_identity(draft, monkeypatch):
    graph, _, _ = draft
    protocol_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    stored = deepcopy(graph)
    stored['nodes'][1]['data']['config']['prompt'] = 'Stored draft.'
    async def owned(*args):
        return SimpleNamespace(graph=stored, experiment_id=None)
    monkeypatch.setattr(api, '_get_owned_protocol', owned)
    app = FastAPI()
    app.include_router(api.router)
    async def current_user():
        return user

    async def database_session():
        yield None

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = database_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        url = f'/protocols/{protocol_id}/nodes/agent/prompt-preview'
        result = await client.post(url, json={'graph': graph, 'row_index': 2})
        assert result.status_code == 200, result.text
        assert result.json()['dataset_row']['row_index'] == 2
        assert result.json()['text'].startswith('Predict.')
        result = await client.post(url, json={})
        assert result.json()['text'].startswith('Stored draft.')
        assert (await client.post(url, json={'row_index': True})).status_code == 422
        assert (await client.post(url, json={'row_index': 8})).status_code == 422
