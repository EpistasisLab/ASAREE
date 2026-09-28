"""Typed sentinel for PATCH-style omit-vs-null request fields."""

from __future__ import annotations

from typing import Final


class UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = UnsetType()
