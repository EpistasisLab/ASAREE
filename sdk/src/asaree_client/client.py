"""Synchronous ASAREE API client — sync-only, matching the notebook driver's usage."""

from __future__ import annotations

import os
from typing import Any

import httpx

from asaree_client._transport import RetryPolicy, build_sync_client, raise_for_status
from asaree_client.exceptions import AsareeError
from asaree_client.resources.agents import Agents
from asaree_client.resources.auth import Auth
from asaree_client.resources.datasets import Datasets
from asaree_client.resources.experiments import Experiments
from asaree_client.resources.llm_settings import LLMSettings
from asaree_client.resources.okf import OKF
from asaree_client.resources.protocols import Protocols
from asaree_client.resources.runs import Runs
from asaree_client.resources.skills import Skills
from asaree_client.resources.tools import Tools
from asaree_client.resources.users import Users


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

    Authenticate with either a per-user API key (``X-API-Key``) or a session
    access token (``Authorization: Bearer``). The auth resource can establish
    and refresh a session on an initially unauthenticated client.

    Constructor arguments override environment variables: ``base_url`` reads
    ``ASAREE_BASE_URL``, ``api_key`` reads ``ASAREE_API_KEY``,
    ``access_token`` reads ``ASAREE_ACCESS_TOKEN``, and ``timeout`` reads
    ``ASAREE_TIMEOUT``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.base_url = _resolve_base_url(base_url)
        if api_key is not None and access_token is not None:
            raise AsareeError("Pass either api_key or access_token, not both.")
        if api_key is None and access_token is None:
            api_key = os.environ.get("ASAREE_API_KEY")
            access_token = os.environ.get("ASAREE_ACCESS_TOKEN")
            if api_key and access_token:
                raise AsareeError("Set either ASAREE_API_KEY or ASAREE_ACCESS_TOKEN, not both.")
        self._refresh_token = refresh_token or os.environ.get("ASAREE_REFRESH_TOKEN")
        self._http = build_sync_client(
            base_url=self.base_url,
            api_key=api_key,
            access_token=access_token,
            timeout=_resolve_timeout(timeout),
            policy=retry_policy,
        )
        self.agents = Agents(self)
        self.runs = Runs(self)
        self.experiments = Experiments(self)
        self.protocols = Protocols(self)
        self.datasets = Datasets(self)
        self.tools = Tools(self)
        self.llm_settings = LLMSettings(self)
        self.skills = Skills(self)
        self.okf = OKF(self)
        self.auth = Auth(self)
        self.users = Users(self)

    def _set_session_tokens(self, access_token: str, refresh_token: str) -> None:
        self._http.headers.pop("X-API-Key", None)
        self._http.headers["Authorization"] = f"Bearer {access_token}"
        self._refresh_token = refresh_token

    def _clear_session_tokens(self) -> None:
        self._http.headers.pop("Authorization", None)
        self._refresh_token = None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, f"/api{path}", **kwargs)
        raise_for_status(response)
        if response.status_code == 204:
            return None
        return response.json()

    def _get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def _get_bytes(self, path: str, **kwargs: Any) -> bytes:
        response = self._http.request("GET", f"/api{path}", **kwargs)
        raise_for_status(response)
        return response.content

    def _post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def _put(self, path: str, **kwargs: Any) -> Any:
        return self._request("PUT", path, **kwargs)

    def _patch(self, path: str, **kwargs: Any) -> Any:
        return self._request("PATCH", path, **kwargs)

    def _delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def version(self) -> str:
        """Return the running ASAREE app version."""
        return str(self._get("/version")["version"])

    def health(self) -> dict[str, str]:
        """Return the server health payload."""
        response = self._http.request("GET", "/health")
        raise_for_status(response)
        return dict(response.json())

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AsareeClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
