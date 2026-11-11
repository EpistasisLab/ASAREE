"""Explicit destination selections and read-only setup inspection."""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from motoro.models.database import system_session
from motoro.models.mcp_server import MCPServerConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell import FactorialCell
from asaree.models.package_import import ImportedIdentity, PackageImport
from asaree.models.protocol import Protocol
from asaree.models.user_llm_setting import UserLLMSetting
from asaree.services.package_connections import compatible


async def runtime_requirement_issues(db: AsyncSession, job: PackageImport, graph: dict[str, Any],
                                     design: dict[str, Any] | None) -> list[dict[str, Any]]:
    from asaree.services import package_imports
    from asaree.services.experiment_packages import node_variants
    from asaree.services.package_reconstruction import identity
    from asaree.services.package_validation import document

    implicit = {'asaree.system:asaree.mcp_servers.workspace_server': 'dataset',
                'asaree.system:scikit_learn_mcp': 'dataset',
                'asaree.system:asaree.mcp_servers.script_server': 'script'}
    issues: list[dict[str, Any]] = []
    async with system_session(reason='validate imported runtime requirements without connecting') as resources:
        for path in (package_imports.directory(job.id) / 'payload/requirements/mcp').glob('*.json'):
            requirement = document(path, package_imports.limits())
            if requirement.get('knowledge_kind'):
                continue
            binding = await db.get(ImportedIdentity, (job.id, 'runtime_mcp', requirement['id']))
            local = binding.local_id if binding else identity(job, 'mcp', requirement['id'])
            affected = []
            for node in graph.get('nodes', []):
                if (node.get('type') == implicit.get(requirement.get('service_identity'))
                    or any(((variant.get('data') or {}).get('config') or {}).get('server_id') == str(local)
                           for variant in node_variants(node, design))):
                    affected.append(node['id'])
            if not affected:
                continue
            server = await resources.scalar(select(MCPServerConfig).where(MCPServerConfig.id == local,
                (MCPServerConfig.owner_id == job.owner_id) | MCPServerConfig.is_system.is_(True)))
            if server is None or not compatible(requirement, server):
                issues.extend({'node_id': node, 'resource_id': requirement['id'],
                    'message': f'Select a compatible destination connection for {requirement["name"]}.'}
                    for node in affected)
    return issues


async def validate_model_connection(db: AsyncSession, job: PackageImport, config: dict[str, Any],
                                    kind: str, node_id: str) -> None:
    provider = config.get('provider') or kind.removeprefix('model_').removeprefix('llm_')
    setting = await db.scalar(select(UserLLMSetting).where(
        UserLLMSetting.user_id == job.owner_id, UserLLMSetting.provider == provider))
    selection = await db.get(ImportedIdentity, (job.id, 'runtime_provider', provider))
    if setting is None or selection is None or selection.local_id != setting.id:
        raise ValueError(f'Select the saved destination {provider} connection for Model node {node_id}.')
    for fallback in config.get('fallback_models') or []:
        await validate_model_connection(db, job, fallback, kind, node_id)


async def setup_options(db: AsyncSession, job: PackageImport) -> dict[str, Any]:
    from asaree.models.experiment import ResearchExperiment
    from asaree.services import package_imports
    from asaree.services.experiment_packages import node_variants
    from asaree.services.package_reconstruction import readiness_issues
    from asaree.services.package_validation import document

    requirements = []
    async with system_session(reason='list authorized import destination connections without connecting') as resources:
        servers = list((await resources.scalars(select(MCPServerConfig).where(
            (MCPServerConfig.owner_id == job.owner_id) | MCPServerConfig.is_system.is_(True)))).all())
        for path in (package_imports.directory(job.id) / 'payload/requirements/mcp').glob('*.json'):
            source = document(path, package_imports.limits())
            if source.get('knowledge_kind'):
                continue
            binding = await db.get(ImportedIdentity, (job.id, 'runtime_mcp', source['id']))
            requirements.append({'source_id': source['id'], 'name': source['name'],
                'required_tools': source.get('required_tool_names', source.get('tool_names', [])),
                'selected_id': str(binding.local_id) if binding else None,
                'candidates': [{'id': str(server.id), 'name': server.name} for server in servers
                               if compatible(source, server)]})
    protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id))
    experiment = await db.get(ResearchExperiment, job.experiment_id)
    required_providers: set[str] = set()

    def require_provider(config: dict[str, Any], kind: str) -> None:
        required_providers.add(config.get('provider') or kind.removeprefix('model_').removeprefix('llm_'))
        for fallback in config.get('fallback_models') or []:
            require_provider(fallback, kind)

    for node in (protocol.graph if protocol else {}).get('nodes', []):
        if str(node.get('type', '')).startswith(('model_', 'llm_')):
            for variant in node_variants(node, experiment.design_spec if experiment else None):
                require_provider((variant.get('data') or {}).get('config') or {}, node['type'])
    settings = (await db.scalars(select(UserLLMSetting).where(UserLLMSetting.user_id == job.owner_id,
        UserLLMSetting.provider.in_(required_providers)))).all()
    providers = []
    for setting in settings:
        binding = await db.get(ImportedIdentity, (job.id, 'runtime_provider', setting.provider))
        providers.append({'provider': setting.provider, 'id': str(setting.id),
                          'selected': binding is not None and binding.local_id == setting.id})
    issues = await readiness_issues(db, job)
    return {'ready': not issues, 'issues': issues, 'mcp': requirements, 'providers': providers}


def rebind_definition(graph: dict[str, Any], design: dict[str, Any] | None,
                      old: str, destination: MCPServerConfig, *, source_graph: dict[str, Any] | None = None,
                      source_design: dict[str, Any] | None = None, source_id: str | None = None,
                      source_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Rewrite schema-owned MCP references, including bound alternatives only."""
    graph, design = deepcopy(graph), deepcopy(design)
    factors = {factor['name']: factor for factor in (design or {}).get('factors', [])}
    source_nodes = {node['id']: node for node in (source_graph or {}).get('nodes', [])}
    source_factors = {factor['name']: factor for factor in (source_design or {}).get('factors', [])}

    def belongs(value: Any) -> bool:
        if source_graph is None:
            return True
        if isinstance(value, str):
            return value == source_id
        return isinstance(value, dict) and (
            value.get('server_id') == source_id or
            (not value.get('server_id') and value.get('server_name') == source_name))

    def config(value: dict[str, Any]) -> None:
        if value.get('server_id') == old:
            value.update(server_id=str(destination.id), server_name=destination.name)

    for node in graph.get('nodes', []):
        if node.get('type') not in {'mcp_tool', 'mcp_client_tool', 'mcp_scikit_learn'}:
            continue
        data = node.get('data') or {}
        original = (source_nodes.get(node['id'], {}).get('data') or {})
        if belongs(original.get('config')):
            config(data.get('config') or {})
        for path, name in (data.get('factor_bindings') or {}).items():
            factor = factors.get(name)
            if factor is None:
                continue
            original_bindings = original.get('factor_bindings') or {}
            original_name = original_bindings.get(path) or original_bindings.get('config.server_id')
            original_levels = source_factors.get(original_name, {}).get('levels', [])
            if path == 'config':
                for index, level in enumerate(factor.get('levels', [])):
                    origin = original_levels[index] if index < len(original_levels) else None
                    if isinstance(level, dict) and belongs(origin):
                        config(level)
            elif path == 'config.server_id':
                factor['levels'] = [str(destination.id) if level == old and belongs(
                    original_levels[index] if index < len(original_levels) else None) else level
                    for index, level in enumerate(factor.get('levels', []))]
    return graph, design


async def select_connection(db: AsyncSession, job: PackageImport, kind: str,
                            source: str, destination: uuid.UUID) -> None:
    from asaree.models.experiment import ResearchExperiment
    from asaree.services import package_imports
    from asaree.services.package_reconstruction import identity
    from asaree.services.package_validation import canonical_identity, document

    if kind == 'provider':
        setting = await db.scalar(select(UserLLMSetting).where(UserLLMSetting.id == destination,
            UserLLMSetting.user_id == job.owner_id, UserLLMSetting.provider == source))
        if setting is None:
            raise ValueError('Choose an authorized saved connection for this provider.')
        mapping_kind = 'runtime_provider'
    elif kind == 'mcp':
        source = canonical_identity(source)
        path = package_imports.directory(job.id) / f'payload/requirements/mcp/{source}.json'
        requirement = document(path, package_imports.limits())
        async with system_session(reason='select authorized destination connection without execution') as resources:
            server = await resources.scalar(select(MCPServerConfig).where(MCPServerConfig.id == destination,
                (MCPServerConfig.owner_id == job.owner_id) | MCPServerConfig.is_system.is_(True)))
            if server is None or not compatible(requirement, server):
                raise ValueError('Destination connection is unavailable or lacks the required service/tools.')
            if requirement.get('knowledge_kind'):
                raise ValueError('Packaged knowledge content must use its owned imported registration.')
            previous = await db.get(ImportedIdentity, (job.id, 'runtime_mcp', source))
            old = str(previous.local_id if previous else identity(job, 'mcp', source))
            protocol = await db.scalar(select(Protocol).where(Protocol.experiment_id == job.experiment_id)
                                       .with_for_update())
            experiment = await db.scalar(select(ResearchExperiment).where(
                ResearchExperiment.id == job.experiment_id).with_for_update())
            if protocol is None or experiment is None or experiment.locked_at is not None:
                raise ValueError('Unlock the editable experiment before changing connections.')
            old_design = deepcopy(experiment.design_spec)
            source_root = package_imports.directory(job.id) / 'payload/publication'
            source_graph = document(source_root / 'canvas.json', package_imports.limits())
            source_settings = document(source_root / 'settings.json', package_imports.limits())
            source_design = source_settings.get('design_spec')
            legacy_design = source_root.parent / 'design/revision.json'
            if source_design is None and legacy_design.exists():
                source_design = document(legacy_design, package_imports.limits()).get('design_spec')
            protocol.graph, experiment.design_spec = rebind_definition(protocol.graph, old_design, old, server,
                source_graph=source_graph, source_design=source_design,
                source_id=source, source_name=requirement['name'])
            # Planned imported cells must follow the same typed factor rewrite.
            old_factors = {f['name']: f.get('levels', []) for f in (old_design or {}).get('factors', [])}
            new_factors = {f['name']: f.get('levels', []) for f in (experiment.design_spec or {}).get('factors', [])}
            # Published designs are immutable; normal generation prepares a new revision.
            from asaree.models.protocol_revision import ProtocolRevision
            from asaree.services.design_revisions import get_current_revision

            design = await get_current_revision(db, experiment.id)
            published = design and await db.scalar(select(ProtocolRevision.id).where(
                ProtocolRevision.design_revision_id == design.id))
            if design and not published:
                design.design_spec = deepcopy(experiment.design_spec)
                for cell in (await db.scalars(select(FactorialCell).where(
                    FactorialCell.design_revision_id == design.id))).all():
                    values = deepcopy(cell.factor_values) or {}
                    provenance = await db.scalar(select(ImportedIdentity).where(ImportedIdentity.import_id == job.id,
                        ImportedIdentity.kind == 'cell', ImportedIdentity.local_id == cell.id))
                    source_values = ((provenance.provenance.get('source') or {}).get('factor_values') or {}
                                     if provenance else {})
                    original_factors = {f['name']: f.get('levels', [])
                                        for f in (source_design or {}).get('factors', [])}
                    for name, value in values.items():
                        levels = old_factors.get(name, [])
                        if value in levels:
                            index = levels.index(value)
                            original_levels = original_factors.get(name, [])
                            if name in source_values and source_values[name] in original_levels:
                                index = original_levels.index(source_values[name])
                            if index < len(new_factors[name]):
                                values[name] = deepcopy(new_factors[name][index])
                    cell.factor_values = values
            plan = deepcopy(experiment.measurement_plan)
            original_producers = (source_settings.get('measurement_plan') or {}).get('producers', [])
            for index, producer in enumerate((plan or {}).get('producers', [])):
                if producer.get('producer_id') == 'asaree.mcp_tool':
                    config = producer.get('config') or {}
                    original = original_producers[index] if index < len(original_producers) else {}
                    if config.get('server_id') == old and (original.get('config') or {}).get('server_id') == source:
                        config['server_id'] = str(destination)
            experiment.measurement_plan = plan
        mapping_kind = 'runtime_mcp'
    else:
        raise ValueError('Unknown destination connection kind.')
    binding = await db.get(ImportedIdentity, (job.id, mapping_kind, source))
    if binding is None:
        db.add(ImportedIdentity(import_id=job.id, kind=mapping_kind, source_id=source,
            local_id=destination, provenance={'selection': 'explicit'}))
    else:
        binding.local_id, binding.provenance = destination, {'selection': 'explicit'}
    await db.flush()
