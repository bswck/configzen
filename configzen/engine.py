from __future__ import annotations

import contextlib
import functools
import importlib
from collections.abc import ByteString, MutableMapping
from typing import Any, ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from configzen.config import ConfigContext


__all__ = (
    'Engine',
    'convert',
    'converter',
    'load',
    'loader',
    'loaders',
    'no_loader_strategy',
    'get_engine_class',
)


class Engine:
    name: ClassVar[str]
    registry: dict[str, type[Engine]] = {}

    def __init__(self, sections, **kwargs) -> None:
        self.sections = sections
        self.engine_options = kwargs

    def load(
        self,
        blob: str | ByteString | None,
        defaults: MutableMapping[str, Any] | None = None,
    ) -> MutableMapping[str, Any]:
        """Load a config from a blob.

        Parameters
        ----------
        blob : str | ByteString | None
            The blob to load.
        defaults : MutableMapping[str, Any] | None
            The default values to use.

        Returns
        -------
        MutableMapping[str, Any]
            The loaded config.
        """
        raise NotImplementedError

    def _dump(self, config: MutableMapping[str, Any]) -> str | ByteString:
        raise NotImplementedError

    def dump(self, config: MutableMapping[str, Any], autoconvert: bool = True):
        """Dump a config to a blob.

        Parameters
        ----------
        config : MutableMapping[str, Any]
            The config to dump.
        autoconvert : bool
            Whether to automatically convert the config.

        Returns
        -------
        str | ByteString
            The dumped config blob.
        """
        if autoconvert:
            config = self.convert(config)
        return self._dump(config)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.name = name = cls.name.casefold()
        Engine.registry[name] = cls

    @functools.singledispatchmethod
    def convert(self, obj: Any) -> Any:
        """Engine-specific conversion of config values.

        Parameters
        ----------
        obj : Any
            The object to convert.

        Returns
        -------
        Any
            The converted object.
        """
        return convert(obj)

    @functools.singledispatchmethod
    def loader(self, factory, value) -> Any:
        """Engine-specific loading of config values.

        Parameters
        ----------
        factory : Any
            The factory to use.
        value : Any
            The value to load.

        Returns
        -------
        Any
            The loaded value.
        """
        return load(factory, value)


@functools.singledispatch
def convert(obj: Any) -> Any:
    """Default conversion of config objects.

    Parameters
    ----------
    obj : Any
        The object to convert.

    Returns
    -------
    Any
        The converted object.
    """
    return obj


def no_loader_strategy(cls, value, context):
    if isinstance(value, cls):
        return value
    return cls(value)


loaders = functools.singledispatch(no_loader_strategy)


def load(factory: Any, value: Any, context: ConfigContext) -> Any:
    """Default loading of config objects.

    Parameters
    ----------
    factory : Any
        The factory to use.
    value : Any
        The value to load.
    context : ConfigContext
        The config context.

    Returns
    -------
    Any
        The loaded value.
    """
    return loaders.dispatch(factory)(factory, value, context)


_MISSING = object()


def converter(func, obj=_MISSING):
    """Register a converter for an object within a convenient decorator.

    Parameters
    ----------
    func : Callable[[Any], Any]
        The converter function.

    obj : Any
        The object to register the converter for.

    Returns
    -------
    Callable[[Any], Any]
        The converter function.
    """
    if obj is _MISSING:
        return functools.partial(converter, func)
    convert.register(obj, func)
    return obj


def loader(func, factory=_MISSING):
    """Register a loader for an object within a convenient decorator.

    Parameters
    ----------
    func : Callable[[Any], Any]
        The loader function.

    factory : Any
        The factory to register the loader for.

    Returns
    -------
    Callable[[Any], Any]
        The loader function.

    """
    if factory is _MISSING:
        return functools.partial(loader, func)

    loaders.register(factory, func)
    return factory


def get_engine_class(engine_name: str) -> type[Engine]:
    """Get the engine class for the given engine name.

    Parameters
    ----------
    engine_name : str
        The name of the engine.

    Returns
    -------
    type[Engine]
        The engine class.

    Raises
    ------
    KeyError
        If the engine is not found.
    """
    engine_name = engine_name.casefold()
    if engine_name not in Engine.registry:
        with contextlib.suppress(ImportError):
            importlib.import_module(f"configzen.engines.{engine_name}_engine")

    return Engine.registry[engine_name]
