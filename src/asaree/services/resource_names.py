"""Shared display-name allocation; database uniqueness remains authoritative."""
from collections.abc import Iterable


def available_name(preferred: str, existing: Iterable[str], *, maximum: int = 255,
                   skill: bool = False) -> str:
    names = set(existing)
    base = preferred.strip()[:maximum]
    candidate = base
    number = 2
    while candidate in names:
        suffix = f'-{number}' if skill else f' ({number})'
        candidate = base[:maximum - len(suffix)].rstrip('-' if skill else ' ') + suffix
        number += 1
    return candidate
