"""Recursive freezing helpers for public recommendation DTOs."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_value(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(child) for child in value)
    return value


def freeze_mapping(value: Mapping | None) -> Mapping:
    return freeze_value(value or {})


__all__ = ["freeze_mapping", "freeze_value"]
