"""Durable preparation of immutable original-source metadata, outside HTTP."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.models.dataset import RegisteredDataset
from asaree.services.dataset_row_csv import DatasetRowCsvError, RowSource
from asaree.services.indexed_row_source import read_indexed_row_source

logger = logging.getLogger(__name__)


def schema_payload(source: RowSource) -> dict[str, Any]:
    return {
        "ready": True,
        "raw_sha256": source.raw_sha256,
        "columns": list(source.columns),
        "row_count": len(source.rows),
    }


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
            source = await asyncio.to_thread(
                read_indexed_row_source,
                dataset_id=str(dataset.id),
                raw_path=dataset.raw_path,
                raw_sha256=dataset.raw_sha256,
                cache_root=Path(get_settings().result_row_index_dir),
            )
            dataset.row_schema = schema_payload(source)
        except DatasetRowCsvError:
            logger.exception("row_schema_preparation_failed", extra={"dataset_id": str(dataset.id)})
            dataset.row_schema = {
                "status": "failed",
                "raw_sha256": dataset.raw_sha256,
                "error": "Original CSV could not be verified or indexed.",
            }
