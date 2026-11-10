"""Durable preparation of immutable original-source metadata, outside HTTP."""

import base64
import logging
from typing import Any

from sqlalchemy import func, select

from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.services.dataset_row_csv import DatasetRowCsvError, RowSource
from asaree.services.indexed_row_source import decode_strings, encode_strings, read_indexed_row_source

logger = logging.getLogger(__name__)


def schema_payload(source: RowSource) -> dict[str, Any]:
    return {
        "ready": True,
        "raw_sha256": source.raw_sha256,
        **({"columns_data": base64.b64encode(encode_strings(source.columns)).decode("ascii")}
           if any("\x00" in column for column in source.columns) else {"columns": list(source.columns)}),
        "row_count": len(source.rows),
    }


def schema_columns(metadata: dict[str, Any]) -> list[str]:
    if "columns_data" in metadata:
        return list(decode_strings(base64.b64decode(metadata["columns_data"], validate=True)))
    columns = metadata["columns"]
    if not isinstance(columns, list) or any(not isinstance(column, str) for column in columns):
        raise DatasetRowCsvError("invalid_csv", "invalid cached CSV columns")
    return columns


async def prepare_row_schemas(_ctx: dict[str, Any]) -> None:
    async with get_session() as db:
        if not await db.scalar(select(func.pg_try_advisory_xact_lock(73912843))):
            return
        dataset = await db.scalar(
            select(RegisteredDataset)
            .where(RegisteredDataset.row_schema["status"].astext == "pending")
            .order_by(RegisteredDataset.updated_at)
            .limit(1)
        )
        if dataset is None:
            return
        try:
            source = await read_indexed_row_source(db, dataset_id=dataset.id, owner_id=dataset.owner_id)
            dataset.row_schema = schema_payload(source)
        except DatasetRowCsvError:
            logger.exception("row_schema_preparation_failed", extra={"dataset_id": str(dataset.id)})
            dataset.row_schema = {
                "status": "failed",
                "raw_sha256": dataset.raw_sha256,
                "error": "Original CSV could not be verified or indexed.",
            }
