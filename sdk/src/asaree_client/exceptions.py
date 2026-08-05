"""ASAREE SDK exception hierarchy — trimmed to the statuses ASAREE actually returns."""

from __future__ import annotations


class AsareeError(Exception):
    """Base exception for all ASAREE SDK errors."""


class AsareeConnectionError(AsareeError):
    """Failed to connect to the ASAREE server."""


class AsareeTimeoutError(AsareeError):
    """Request to the ASAREE server timed out."""


class AsareeAPIError(AsareeError):
    """ASAREE API returned an error response."""

    def __init__(self, status_code: int, detail: str, *, body_json: dict[str, object] | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.body_json = body_json
        super().__init__(f"HTTP {status_code}: {detail}")


class AsareeBadRequestError(AsareeAPIError):
    """ASAREE API returned 400 Bad Request."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=400, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeAuthenticationError(AsareeAPIError):
    """ASAREE API returned 401 Unauthorized — missing or invalid X-API-Key."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=401, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeNotFoundError(AsareeAPIError):
    """ASAREE API returned 404 Not Found."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=404, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeConflictError(AsareeAPIError):
    """ASAREE API returned 409 Conflict (e.g. a duplicate experiment/dataset name)."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=409, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeUnprocessableEntityError(AsareeAPIError):
    """ASAREE API returned 422 Unprocessable Entity (a validation error)."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=422, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeUpstreamError(AsareeAPIError):
    """ASAREE API returned 502 — a registered MCP server failed or refused a call."""

    def __init__(self, detail: str, **kwargs: object) -> None:
        super().__init__(status_code=502, detail=detail, **kwargs)  # type: ignore[arg-type]


class AsareeServerError(AsareeAPIError):
    """ASAREE API returned some other 5xx server error."""
