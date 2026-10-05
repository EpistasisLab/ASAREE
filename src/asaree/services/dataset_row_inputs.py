"""Validation and normalization for Dataset-to-Agent edge input settings."""

from __future__ import annotations

_MISSING = object()


class DatasetRowInputError(ValueError):
    """A Dataset input configuration that does not match the edge contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"dataset_row.{code}: {message}")


def normalize_dataset_input(value: object = _MISSING) -> dict[str, str | list[str]]:
    """Return a fresh canonical Dataset edge input configuration.

    An omitted configuration keeps the historical whole-dataset behavior.
    Explicit JSON null is malformed and must be distinguished from omission.
    """
    if value is _MISSING:
        return {"mode": "whole_dataset"}
    if not isinstance(value, dict):
        raise DatasetRowInputError("invalid_configuration", "configuration must be an object")

    unknown_fields = set(value) - {"mode", "columns"}
    if unknown_fields:
        raise DatasetRowInputError("invalid_configuration", "configuration contains unknown fields")

    mode = value.get("mode")
    if mode == "whole_dataset":
        if "columns" not in value or value["columns"] == []:
            return {"mode": "whole_dataset"}
        raise DatasetRowInputError("invalid_columns", "whole_dataset does not accept columns")

    if mode == "per_row":
        columns = value.get("columns")
        if not isinstance(columns, list) or not columns:
            raise DatasetRowInputError("invalid_columns", "per_row requires a non-empty columns array")
        if any(not isinstance(column, str) or not column.strip() for column in columns):
            raise DatasetRowInputError("invalid_columns", "columns must be non-empty, non-whitespace strings")
        if len(set(columns)) != len(columns):
            raise DatasetRowInputError("invalid_columns", "columns must be distinct")
        return {"mode": "per_row", "columns": list(columns)}

    raise DatasetRowInputError("invalid_mode", "mode must be 'whole_dataset' or 'per_row'")
