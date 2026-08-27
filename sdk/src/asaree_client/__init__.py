"""Trimmed synchronous SDK for ASAREE."""

from __future__ import annotations

from asaree_client._transport import RetryPolicy
from asaree_client.client import AsareeClient
from asaree_client.exceptions import (
    AsareeAPIError,
    AsareeAuthenticationError,
    AsareeBadRequestError,
    AsareeConflictError,
    AsareeConnectionError,
    AsareeError,
    AsareeNotFoundError,
    AsareeServerError,
    AsareeTimeoutError,
    AsareeUnprocessableEntityError,
    AsareeUpstreamError,
)
from asaree_client.models import (
    Agent,
    Cell,
    DesignRevision,
    Experiment,
    ExperimentArtifact,
    LLMSetting,
    MCPServer,
    RegisteredDataset,
    Run,
    RunStep,
    ToolCallResult,
    WorkspaceEvent,
)

try:
    # Written by hatch-vcs at build time from the repo's git tag.
    from asaree_client._version import __version__
except ImportError:
    # Imported from a source tree that was never built. _transport sends this as
    # the User-Agent, so it has to resolve to something -- say "unknown" rather
    # than a number that would go stale the way the old hardcoded one did.
    __version__ = "0.0.0+unknown"

__all__ = [
    "Agent",
    "AsareeAPIError",
    "AsareeAuthenticationError",
    "AsareeBadRequestError",
    "AsareeClient",
    "AsareeConflictError",
    "AsareeConnectionError",
    "AsareeError",
    "AsareeNotFoundError",
    "AsareeServerError",
    "AsareeTimeoutError",
    "AsareeUnprocessableEntityError",
    "AsareeUpstreamError",
    "Cell",
    "DesignRevision",
    "Experiment",
    "ExperimentArtifact",
    "LLMSetting",
    "MCPServer",
    "RegisteredDataset",
    "RetryPolicy",
    "Run",
    "RunStep",
    "ToolCallResult",
    "WorkspaceEvent",
]
