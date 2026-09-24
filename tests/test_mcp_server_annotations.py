import uuid
from types import SimpleNamespace

import pytest

from asaree.api import mcp_servers


class _Annotations:
    def model_dump(self, *, by_alias: bool, exclude_none: bool) -> dict[str, bool]:
        assert by_alias is True
        assert exclude_none is True
        return {"readOnlyHint": True}


@pytest.mark.asyncio
async def test_capabilities_include_live_mcp_tool_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Session:
        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[SimpleNamespace(name="score", annotations=_Annotations())])

    client = SimpleNamespace(_session=_Session())
    server_id = uuid.uuid4()
    registry = SimpleNamespace(servers={server_id: SimpleNamespace(client=client)})
    monkeypatch.setattr(mcp_servers, "get_registry", lambda: registry)
    config = SimpleNamespace(
        id=server_id,
        name="quality",
        capabilities={"tools": [{"name": "score", "description": "Scores an answer", "input_schema": {}}]},
    )

    capabilities = await mcp_servers._capabilities_with_tool_annotations(config)

    assert capabilities is not None
    assert capabilities["tools"][0]["annotations"] == {"readOnlyHint": True}
