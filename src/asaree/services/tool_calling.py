"""Whether a model can be sent function schemas -- as a *tri-state*.

Motoro's ``model_supports_tool_calling`` answers the same question but collapses
"litellm has never heard of this model" into ``False``, which is the right
conservative default when the answer picks an execution pattern. It is the wrong
answer to show a user: an Azure Foundry deployment name is never in litellm's
map, so every Azure agent on the canvas would wear a warning saying it cannot
call tools when in fact nobody knows.

So this returns ``None`` for an unrecognised model and lets the caller say
nothing. Same oracle, same model string, one more outcome.
"""

from __future__ import annotations

import litellm
from motoro.schemas.agent import ModelConfig
from motoro.services.llm_service import model_supports_tool_calling


def model_tool_calling_support(provider: str, model: str) -> bool | None:
    """``True``/``False`` when litellm knows *model*, ``None`` when it doesn't."""
    if not model:
        return None
    try:
        config = ModelConfig(provider=provider, model=model)
    except Exception:  # noqa: BLE001 -- an unknown provider is a "can't tell", not a crash
        return None
    if not _litellm_knows(config):
        return None
    return model_supports_tool_calling(config)


def _litellm_knows(config: ModelConfig) -> bool:
    # Deliberately the same string model_supports_tool_calling builds -- an
    # answer derived from a different one would be about a different model.
    prefix = {"azure_foundry": "azure_ai", "local": "openai"}.get(config.provider.value)
    model_str = f"{prefix}/{config.model}" if prefix else config.model
    try:
        litellm.get_model_info(model_str)
    except Exception:  # noqa: BLE001 -- litellm raises a plain Exception for an unmapped model
        return False
    return True
