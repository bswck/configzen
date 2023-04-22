import dataclasses

from configzen.engine import converter as register_converter, loader as register_loader


class Section:
    def __init_subclass__(cls, converter=None, loader=None):
        super().__init_subclass__()
        if converter is not None:
            register_converter(converter, cls)
        if loader is not None:
            register_loader(loader, cls)


def dict_converter(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def dict_loader(cls, value):
    return cls(**value)


class Dict(Section):
    def __init_subclass__(cls, converter=dict_converter, loader=dict_loader):
        super().__init_subclass__(converter=converter, loader=loader)


def tuple_converter(obj):
    if dataclasses.is_dataclass(obj):
        return list(dataclasses.astuple(obj))
    return list(obj)


def tuple_loader(cls, value):
    return cls(*value)


class Tuple(Section):
    def __init_subclass__(cls, converter=tuple_converter, loader=tuple_loader):
        super().__init_subclass__(converter=converter, loader=loader)
