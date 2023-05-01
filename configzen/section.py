import dataclasses

from configzen.engine import converter as register_converter, loader as register_loader


class Section:
    __config_meta__ = None

    def __init_subclass__(cls, converter=None, loader=None):
        super().__init_subclass__()
        if converter is not None:
            register_converter(converter, cls)
        if loader is not None:
            register_loader(loader, cls)


def default_dict_converter(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def default_dict_loader(cls, value):
    if isinstance(value, cls):
        return value
    return cls(**value)


class DictSection(Section):
    def __init_subclass__(cls, converter=default_dict_converter, loader=default_dict_loader):
        super().__init_subclass__(converter=converter, loader=loader)


def default_tuple_converter(obj):
    if dataclasses.is_dataclass(obj):
        return list(dataclasses.astuple(obj))
    return list(obj)


def default_tuple_loader(cls, value):
    if isinstance(value, cls):
        return value
    return cls(*value)


class TupleSection(Section):
    def __init_subclass__(cls, converter=default_tuple_converter, loader=default_tuple_loader):
        super().__init_subclass__(converter=converter, loader=loader)
