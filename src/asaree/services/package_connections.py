"""Read-only destination matching. Archive requirements never supply connections."""
from __future__ import annotations

import shlex
from typing import Any

from asaree.services.system_mcp_servers import SYSTEM_MCP_SERVERS


def service_identity(server: Any) -> str | None:
    """Recognize shipped modules in trusted system registrations, independent of paths/names."""
    if not getattr(server, 'is_system', False):
        return None
    try:
        command = shlex.split(server.command or '')
    except ValueError:
        return None
    for index, token in enumerate(command[:-1]):
        if token == '-m' and command[index + 1] in {module for _, module in SYSTEM_MCP_SERVERS}:
            return f'asaree.system:{command[index + 1]}'
    return None


def compatible(requirement: dict[str, Any], server: Any) -> bool:
    required = set(requirement.get('required_tool_names', requirement.get('tool_names')) or [])
    available = {tool.get('name') for tool in (server.capabilities or {}).get('tools', [])
                 if isinstance(tool, dict)}
    return bool(server.command or server.url) and required <= available and (
        not requirement.get('service_identity') or requirement['service_identity'] == service_identity(server))


def automatic_match(requirement: dict[str, Any], servers: list[Any]) -> Any | None:
    if not requirement.get('service_identity'):
        return None
    matches = [server for server in servers if compatible(requirement, server)]
    return matches[0] if len(matches) == 1 else None
