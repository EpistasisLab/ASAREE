from __future__ import annotations

from pydantic import BaseModel


class LLMSetting(BaseModel):
    provider: str
    api_base: str | None
    azure_project_endpoint: str | None = None


class LLMModelInfo(BaseModel):
    id: str
    label: str | None
    supports_temperature: bool
    supports_effort: bool
    effort_levels: list[str]
    supports_tool_calling: bool | None


class LLMModels(BaseModel):
    models: list[LLMModelInfo]
    source: str
    note: str | None


class LLMConnectionCheck(BaseModel):
    provider: str
    status: str
    detail: str
    endpoint: str | None
