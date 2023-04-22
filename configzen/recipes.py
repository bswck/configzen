import dataclasses
from collections.abc import ByteString, Mapping, Sequence

from configzen.engine import converter, loader


def dataclass_loader(factory, value):
    if isinstance(value, Mapping):
        return factory(**value)
    if not isinstance(value, (str, ByteString)) and isinstance(value, Sequence):
        return factory(*value)
    return factory(value)


class Dataclass:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        converter(dataclasses.asdict)(cls)
        loader(dataclass_loader)(cls)
