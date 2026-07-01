"""Helpers for safely transforming GenerationResult output values."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


def iter_output_strings(value: Any) -> Iterator[str]:
    """Yield every string leaf without treating mapping keys as output text."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_output_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from iter_output_strings(child)


def map_output_strings(value: Any, transform: Callable[[str], str]) -> Any:
    """Return a shape-preserving copy with ``transform`` applied to string leaves."""
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: map_output_strings(child, transform) for key, child in value.items()}
    if isinstance(value, list):
        return [map_output_strings(child, transform) for child in value]
    if isinstance(value, tuple):
        return tuple(map_output_strings(child, transform) for child in value)
    return value


def empty_output(output: dict | str) -> dict | str:
    """Preserve the declared top-level output type while rejecting its contents."""
    return {} if isinstance(output, dict) else ""


def remove_output_fragments(output: dict | str, fragments: tuple[str, ...]) -> dict | str:
    """Remove non-empty fragments from every string leaf in an output object."""
    usable = tuple(fragment for fragment in fragments if fragment)
    if not usable:
        return output

    def remove(text: str) -> str:
        for fragment in usable:
            text = text.replace(fragment, "")
        return text

    return map_output_strings(output, remove)


__all__ = [
    "empty_output",
    "iter_output_strings",
    "map_output_strings",
    "remove_output_fragments",
]
