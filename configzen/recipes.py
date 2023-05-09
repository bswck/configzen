from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from configzen.errors import IncompleteConfigurationError

if TYPE_CHECKING:
    from configzen.config import BaseConfigContext, ConfigT


__all__ = (
    "dataclass_convert",
    "dataclass_load",
)


def dataclass_convert(obj: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def dataclass_load(
    cls: type[ConfigT],
    value: Mapping,
    _context: BaseConfigContext[ConfigT],
) -> ConfigT:
    if isinstance(value, cls):
        return value
    if not isinstance(value, (Mapping, Iterable)):
        return cls(value)
    sig = inspect.signature(cls)
    try:
        bound = sig.bind(**value) if isinstance(value, Mapping) else sig.bind(*value)
    except TypeError as exc:
        raise IncompleteConfigurationError(
            cls.__name__ + " configuration is " + exc.args[0],
        ) from None
    return cls(*bound.args, **bound.kwargs)
