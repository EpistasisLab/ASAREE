"""Synchronous ASAREE API client — sync-only, matching the notebook driver's usage."""

from __future__ import annotations

import os
from typing import Any

import httpx

from asaree_client._transport import RetryPolicy, build_sync_client, raise_for_status
from asaree_client.exceptions import AsareeError
from asaree_client.resources.agents import Agents
from asaree_client.resources.datasets import Datasets
from asaree_client.resources.experiments import Experiments
from asaree_client.resources.runs import Runs
from asaree_client.resources.tools import Tools


def _resolve_base_url(base_url: str | None) -> str:
    url = base_url or os.environ.get("ASAREE_BASE_URL")
    if not url:
        raise AsareeError(
            "No base_url provided. Pass base_url to the constructor or set the ASAREE_BASE_URL environment variable."
        )
    return url.rstrip("/")


def _resolve_timeout(timeout: float | httpx.Timeout | None) -> float | httpx.Timeout:
    if timeout is not None:
        return timeout
    env_timeout = os.environ.get("ASAREE_TIMEOUT")
    return float(env_timeout) if env_timeout else 30.0


class AsareeClient:
    """Synchronous ASAREE API client.

    Authenticated with a per-user API token (sent as ``X-API-Key`` —
    ASAREE has no server-wide key, see the SDK README's bootstrap section).

    Constructor arguments override environment variables: ``base_url`` reads
    ``ASAREE_BASE_URL``, ``api_key`` reads ``ASAREE_API_KEY``, ``timeout``
    reads ``ASAREE_TIMEOUT``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.base_url = _resolve_base_url(base_url)
        api_key = api_key or os.environ.get("ASAREE_API_KEY")
        self._http = build_sync_client(
            base_url=self.base_url,
            api_key=api_key,
            timeout=_resolve_timeout(timeout),
            policy=retry_policy,
        )
        self.agents = Agents(self)
        self.runs = Runs(self)
        self.experiments = Experiments(self)
        self.datasets = Datasets(self)
        self.tools = Tools(self)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        raise_for_status(response)
        if response.status_code == 204:
            return None
        return response.json()

    def _get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def _put(self, path: str, **kwargs: Any) -> Any:
        return self._request("PUT", path, **kwargs)

    def _patch(self, path: str, **kwargs: Any) -> Any:
        return self._request("PATCH", path, **kwargs)

    def _delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AsareeClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
