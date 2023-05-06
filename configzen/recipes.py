import dataclasses

from collections.abc import Mapping, Iterable

__all__ = (
    'dataclass_convert',
    'dataclass_load',
)


def dataclass_convert(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def dataclass_load(cls, value, _context):
    if isinstance(value, cls):
        return value
    if isinstance(value, Mapping):
        return cls(**value)
    if isinstance(value, Iterable):
        return cls(*value)
    return cls(value)
