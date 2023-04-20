import importlib
from typing import Any, ClassVar


class Engine:
    name: ClassVar[str]
    _registry = {}

    def load(self, serialized_data: bytes | str, defaults: dict[str, Any] | None = None):
        raise NotImplementedError

    def dump(self, config: dict[str, Any]):
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.name = cls.name.casefold()
        key = cls.name
        namespace = cls._registry
        # #  I don't think we need this
        # #  But I'm leaving it here for now
        # while '.' in key:
        #     family, key = key.split('.', 1)
        #     if not family:
        #         break
        #     namespace = namespace.setdefault(family, {})
        namespace[key] = cls


def get_engine_class(engine_name: str):
    engine_name = engine_name.casefold()
    if engine_name not in Engine._registry:
        try:
            importlib.import_module(f'configzen.engines.{engine_name}_engine')
        except ImportError:
            pass
    return Engine._registry[engine_name]


def to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, '__configzen_to_dict__'):
        obj = obj.__configzen_to_dict__()
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
