"""Audit logging — fire-and-forget append-only user-action trail.

Uses ``get_session()`` (not the request's own ``db``) because this is almost
always called from a FastAPI ``BackgroundTask``, which runs after the
response is sent — by then the request's session dependency has already
closed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request

from asaree.models.audit_log_entry import AuditLogEntry
from asaree.models.database import get_session

logger = logging.getLogger(__name__)


async def log_action(
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Write one audit entry. Never raises — a failed audit write must not
    fail (or retry, or surface to) the request that triggered it."""
    ip_address = None
    user_agent = None
    if request is not None:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    try:
        async with get_session() as db:
            db.add(
                AuditLogEntry(
                    user_id=user_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details=details,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
    except Exception:
        logger.exception("audit_log_failed", extra={"action": action, "resource_type": resource_type})
