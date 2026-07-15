"""Shared, dependency-free domain model helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


JsonValue = Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class FrozenDict(dict):
    """JSON mapping that preserves ``dict`` compatibility without mutation."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("captured JSON is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenDict":
        return self


class FrozenList(list):
    """JSON list that remains list-compatible for deterministic analyzers."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("captured JSON is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenList":
        return self


def freeze_json(value: Any) -> Any:
    """Recursively freeze JSON-like values while preserving dict/list APIs."""

    if isinstance(value, FrozenDict | FrozenList):
        return value
    if isinstance(value, dict):
        return FrozenDict({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze_json(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_json(item) for item in value)
    return value


class FrozenModel(BaseModel):
    """Value object used for immutable captured facts and compiled contracts."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=False,
        protected_namespaces=(),
    )


class DomainModel(BaseModel):
    """Mutable aggregate model; mutation is restricted to repository boundaries."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False, protected_namespaces=())
