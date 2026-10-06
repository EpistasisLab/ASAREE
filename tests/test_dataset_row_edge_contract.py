from __future__ import annotations

import pytest

from asaree.services.dataset_row_inputs import DatasetRowInputError, normalize_dataset_input


def test_missing_configuration_preserves_whole_dataset_behavior() -> None:
    assert normalize_dataset_input() == {"mode": "whole_dataset"}


def test_per_row_configuration_preserves_column_order_and_spelling() -> None:
    configuration = {"mode": "per_row", "columns": ["question", "hint"]}

    assert normalize_dataset_input(configuration) == {
        "mode": "per_row",
        "columns": ["question", "hint"],
    }
    assert configuration == {"mode": "per_row", "columns": ["question", "hint"]}


@pytest.mark.parametrize(
    ("configuration", "code"),
    [
        ({"mode": "per_row", "columns": ["question", "question"]}, "invalid_columns"),
        ({"mode": "per_row", "columns": []}, "invalid_columns"),
        ({"mode": "per_row", "columns": "question"}, "invalid_columns"),
        ({"mode": "per_row", "columns": ["   "]}, "invalid_columns"),
        ({"mode": "whole_dataset", "columns": ["question"]}, "invalid_columns"),
        (None, "invalid_configuration"),
        ({"mode": "row"}, "invalid_mode"),
        ({"mode": "per_row", "columns": ["question"], "extra": True}, "invalid_configuration"),
    ],
)
def test_malformed_configuration_is_rejected_with_a_code(configuration: object, code: str) -> None:
    with pytest.raises(DatasetRowInputError) as exc_info:
        normalize_dataset_input(configuration)

    assert exc_info.value.code == code
    assert str(exc_info.value).startswith(f"dataset_row.{code}: ")


def test_whole_dataset_empty_columns_are_canonicalized_away() -> None:
    assert normalize_dataset_input({"mode": "whole_dataset", "columns": []}) == {"mode": "whole_dataset"}


def test_per_row_results_have_fresh_column_lists() -> None:
    configuration = {"mode": "per_row", "columns": ["question"]}

    first = normalize_dataset_input(configuration)
    second = normalize_dataset_input(configuration)

    assert first is not second
    assert first["columns"] is not configuration["columns"]
    assert first["columns"] is not second["columns"]
