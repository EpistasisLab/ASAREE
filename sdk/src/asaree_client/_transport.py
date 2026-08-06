"""Retry transport for the sync HTTP client.

Trimmed from ares_client._transport: sync-only (no AsyncRetryTransport), and
build_headers supports only X-API-Key — ASAREE's ``get_current_user`` dep
(asaree.deps) never reads an Authorization header, so there is no second
auth method to plumb through.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from asaree_client.exceptions import (
    AsareeAPIError,
    AsareeAuthenticationError,
    AsareeBadRequestError,
    AsareeConflictError,
    AsareeConnectionError,
    AsareeNotFoundError,
    AsareeServerError,
    AsareeTimeoutError,
    AsareeUnprocessableEntityError,
    AsareeUpstreamError,
)

_DEFAULT_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class RetryPolicy:
    """Configurable retry/backoff policy.

    Attributes:
        max_retries: Maximum number of retry attempts (not counting the first try).
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay in seconds between retries.
        jitter: If True, adds random jitter (±25% of computed delay) to each wait.
        retry_statuses: Set of HTTP status codes that trigger a retry.
    """

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: bool = True
    retry_statuses: frozenset[int] = field(default_factory=lambda: _DEFAULT_RETRYABLE_STATUS)

    def compute_delay(self, attempt: int) -> float:
        delay: float = min(self.base_delay * (2**attempt), self.max_delay)
        if self.jitter:
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        return max(delay, 0.0)


def raise_for_status(response: httpx.Response) -> None:
    """Raise an appropriate AsareeError for non-2xx responses."""
    if response.is_success:
        return

    body_json: dict[str, object] | None = None
    detail: str = response.text
    try:
        body_json = response.json()
        if isinstance(body_json, dict):
            detail = str(body_json.get("detail", response.text))
    except Exception:
        pass

    kwargs: dict[str, Any] = {"detail": detail, "body_json": body_json}
    status = response.status_code
    if status == 400:
        raise AsareeBadRequestError(**kwargs)
    if status == 401:
        raise AsareeAuthenticationError(**kwargs)
    if status == 404:
        raise AsareeNotFoundError(**kwargs)
    if status == 409:
        raise AsareeConflictError(**kwargs)
    if status == 422:
        raise AsareeUnprocessableEntityError(**kwargs)
    if status == 502:
        raise AsareeUpstreamError(**kwargs)
    if status >= 500:
        raise AsareeServerError(status_code=status, **kwargs)
    raise AsareeAPIError(status_code=status, **kwargs)


def _effective_max_retries(request: httpx.Request, policy: RetryPolicy) -> int:
    """Per-request retry override: a truthy ``asaree_no_retry`` extension forces 0.

    Callers set ``extensions={"asaree_no_retry": True}`` on a request (e.g. a
    long, non-idempotent direct tool invocation like ``run_model_script``) to
    opt out of automatic retries while keeping the client-wide policy for
    everything else.
    """
    if request.extensions.get("asaree_no_retry"):
        return 0
    return policy.max_retries


class RetryTransport(httpx.BaseTransport):
    """Sync transport wrapper that retries on transient failures."""

    def __init__(self, transport: httpx.BaseTransport, policy: RetryPolicy) -> None:
        self._transport = transport
        self._policy = policy

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        max_retries = _effective_max_retries(request, self._policy)
        for attempt in range(max_retries + 1):
            try:
                response = self._transport.handle_request(request)
                if response.status_code not in self._policy.retry_statuses or attempt == max_retries:
                    return response
                response.close()
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt == max_retries:
                    raise AsareeConnectionError(str(exc)) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt == max_retries:
                    raise AsareeTimeoutError(str(exc)) from exc
            time.sleep(self._policy.compute_delay(attempt))
        raise AsareeConnectionError(str(last_exc))  # pragma: no cover

    def close(self) -> None:
        self._transport.close()


def build_headers(api_key: str | None) -> dict[str, str]:
    from asaree_client import __version__

    headers = {"User-Agent": f"asaree-client/{__version__}"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _build_timeout(timeout: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(timeout)


def build_sync_client(
    base_url: str,
    api_key: str | None,
    timeout: float | httpx.Timeout,
    policy: RetryPolicy | None = None,
) -> httpx.Client:
    transport = RetryTransport(httpx.HTTPTransport(), policy=policy or RetryPolicy())
    return httpx.Client(
        base_url=base_url,
        headers=build_headers(api_key),
        timeout=_build_timeout(timeout),
        transport=transport,
    )
