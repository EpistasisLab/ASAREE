"""Destination matches use trusted service modules and saved capabilities only."""
import uuid
from types import SimpleNamespace

from asaree.services.package_connections import automatic_match, compatible, service_identity
from asaree.services.package_setup import rebind_definition


def server(*, name='Renamed destination', module='asaree_sklearn_eda', tools=('describe',), system=True):
    return SimpleNamespace(id=uuid.uuid4(), name=name, is_system=system,
        command=f'uv run --directory /different/destination python -m {module}', url=None,
        capabilities={'tools': [{'name': tool} for tool in tools]}, env={'SECRET': 'destination'})


def test_builtin_matching_requires_identity_capabilities_and_unique_match():
    destination = server()
    requirement = {'name': 'Source display name', 'service_identity': 'asaree.system:asaree_sklearn_eda',
                   'tool_names': ['describe']}
    assert service_identity(destination) == requirement['service_identity']
    assert automatic_match(requirement, [destination]) is destination
    assert automatic_match(requirement, [destination, server()]) is None
    assert automatic_match(requirement, [server(tools=())]) is None
    assert automatic_match(requirement, [server(system=False)]) is None
    assert automatic_match(requirement, [server(module='asaree_sklearn_model')]) is None
    assert automatic_match({'name': destination.name, 'tool_names': ['describe']}, [destination]) is None
    assert automatic_match({**requirement, 'tool_names': ['describe', 'unused'],
                            'required_tool_names': ['describe']}, [destination]) is destination
    assert destination.env == {'SECRET': 'destination'}


def test_custom_mapping_rewrites_only_typed_factor_references():
    destination = server(system=False)
    old = str(uuid.uuid4())
    graph = {'nodes': [{'id': 'tool', 'type': 'mcp_tool', 'data': {
        'config': {'server_id': old, 'server_name': 'Source', 'opaque': old},
        'factor_bindings': {'config': 'Tools'}}},
        {'id': 'script', 'type': 'script', 'data': {'config': {'code': old}}}]}
    design = {'factors': [{'name': 'Tools', 'levels': [{'server_id': old, 'opaque': old}]}]}
    rewritten, factors = rebind_definition(graph, design, old, destination)
    assert rewritten['nodes'][0]['data']['config']['server_id'] == str(destination.id)
    assert rewritten['nodes'][0]['data']['config']['server_name'] == destination.name
    assert rewritten['nodes'][0]['data']['config']['opaque'] == old
    assert rewritten['nodes'][1]['data']['config']['code'] == old
    assert factors['factors'][0]['levels'][0] == {'server_id': str(destination.id),
                                                'server_name': destination.name, 'opaque': old}
    assert graph['nodes'][0]['data']['config']['server_id'] == old
    assert compatible({'tool_names': ['describe']}, destination)
    assert not compatible({'tool_names': ['missing']}, destination)


def test_reselecting_one_requirement_does_not_rebind_another_sharing_its_connection():
    destination = server(system=False)
    shared, first, second = (str(uuid.uuid4()) for _ in range(3))
    def graph(left, right):
        return {'nodes': [{'id': 'a', 'type': 'mcp_tool', 'data': {
            'config': {'server_id': left}, 'factor_bindings': {'config': 'Connections'}}},
            {'id': 'b', 'type': 'mcp_tool', 'data': {'config': {'server_id': right}}}]}
    current = graph(shared, shared)
    original = graph(first, second)
    original_design = {'factors': [{'name': 'Connections', 'levels': [
        {'server_id': first}, {'server_id': second}]}]}
    current_design = {'factors': [{'name': 'Connections', 'levels': [
        {'server_id': shared}, {'server_id': shared}]}]}
    rewritten, design = rebind_definition(current, current_design, shared, destination,
        source_graph=original, source_design=original_design, source_id=first)
    assert rewritten['nodes'][0]['data']['config']['server_id'] == str(destination.id)
    assert rewritten['nodes'][1]['data']['config']['server_id'] == shared
    assert [level['server_id'] for level in design['factors'][0]['levels']] == [str(destination.id), shared]
