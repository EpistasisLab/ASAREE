"""Small run metadata pages for canvas startup; evidence is a selected read."""

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, or_, select, text

from asaree.api.protocols import _get_owned_protocol
from asaree.deps import CurrentUser, DbSession
from asaree.models.protocol_run import ProtocolRun

router = APIRouter(prefix="/protocols", tags=["protocols"])


@router.get("/{protocol_id}/run-heads")
async def heads(
    protocol_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    await _get_owned_protocol(db, protocol_id, user)
    await db.execute(text("SET LOCAL statement_timeout = '5s'"))
    query = select(
        ProtocolRun.id,
        ProtocolRun.status,
        ProtocolRun.created_at,
        ProtocolRun.protocol_revision_id,
        ProtocolRun.design_revision_id,
        ProtocolRun.replicate_result_id,
        ProtocolRun.row_result_id,
    ).where(
        ProtocolRun.protocol_id == protocol_id, ProtocolRun.is_test_run.is_(False), ProtocolRun.target_node_id.is_(None)
    )
    if cursor is not None:
        try:
            if len(cursor) > 2048:
                raise ValueError("invalid_cursor")
            selected, timestamp, identifier = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if selected != str(protocol_id):
                raise ValueError("invalid_cursor")
            created = datetime.fromisoformat(timestamp)
            run_id = uuid.UUID(identifier)
            query = query.where(
                or_(ProtocolRun.created_at < created, and_(ProtocolRun.created_at == created, ProtocolRun.id < run_id))
            )
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail="invalid_cursor") from exc
    rows = list(
        (
            await db.execute(query.order_by(ProtocolRun.created_at.desc(), ProtocolRun.id.desc()).limit(limit + 1))
        ).mappings()
    )
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps(
                [str(protocol_id), rows[limit - 1]["created_at"].isoformat(), str(rows[limit - 1]["id"])]
            ).encode()
        ).decode()
        if len(rows) > limit
        else None
    )
    return {"items": [dict(row) for row in rows[:limit]], "next_cursor": next_cursor}
