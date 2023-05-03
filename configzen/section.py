import dataclasses

from configzen.engine import convert, loaders


class Section:
    __config_meta__ = None

    def __init_subclass__(cls, converter=None, loader=None):
        super().__init_subclass__()
        if converter is not None:
            convert.register(cls, converter)
        if loader is not None:
            loaders.register(cls, loader)


def mapping_convert(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def mapping_load(cls, value):
    if isinstance(value, cls):
        return value
    return cls(**value)


class MappingSection(Section):
    def __init_subclass__(
        cls,
        converter=mapping_convert,
        loader=mapping_load,
    ):
        super().__init_subclass__(converter=converter, loader=loader)


def sequence_convert(obj):
    if dataclasses.is_dataclass(obj):
        return list(dataclasses.astuple(obj))
    return list(obj)


def sequence_load(cls, value):
    if isinstance(value, cls):
        return value
    return cls(*value)


class SequenceSection(Section):
    def __init_subclass__(
        cls,
        converter=sequence_convert,
        loader=sequence_load,
    ):
        super().__init_subclass__(converter=converter, loader=loader)
