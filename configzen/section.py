import dataclasses
import sys

from collections.abc import Mapping, Iterable

from configzen.engine import convert, loaders

if sys.version_info >= (3, 11):
    from typing import dataclass_transform
else:
    from typing_extensions import dataclass_transform

__all__ = (
    'Section',
    'dataclass_convert',
    'dataclass_load',
)


def dataclass_convert(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def dataclass_load(cls, value):
    if isinstance(value, cls):
        return value
    if isinstance(value, Mapping):
        return cls(**value)
    if isinstance(value, Iterable):
        return cls(*value)
    return cls(value)


@dataclass_transform()
class Section:
    def __init_subclass__(cls, converter=dataclass_convert, loader=dataclass_load):
        super().__init_subclass__()
        if converter is not None:
            convert.register(cls, converter)
        if loader is not None:
            loaders.register(cls, loader)
        dataclasses.dataclass(cls)
