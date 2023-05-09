from __future__ import annotations

import contextlib
import functools
import importlib
from collections.abc import ByteString, Callable, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from configzen.config import BaseConfigContext, ConfigT

__all__ = (
    "Engine",
    "convert",
    "converter",
    "load",
    "loader",
    "loaders",
    "no_loader_strategy",
    "get_engine_class",
)


class Engine:
    name: ClassVar[str]
    registry: dict[str, type[Engine]] = {}

    def __init__(self, sections: dict[str, Callable], **kwargs: Any) -> None:
        self.sections = sections
        self.engine_options = kwargs

    def load(
        self,
        blob: str | ByteString | None,
        defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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

    def dump(
        self,
        config: MutableMapping[str, Any],
        *,
        autoconvert: bool = True,
    ) -> str | ByteString:
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

    def __init_subclass__(cls, **kwargs: Any) -> None:
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
    def loader(
        self,
        factory: type[ConfigT],
        value: Any,
        context: BaseConfigContext[ConfigT],
    ) -> Any:
        """Engine-specific loading of config values.

        Parameters
        ----------
        factory : Any
            The factory to use.
        value : Any
            The value to load.
        context : BaseConfigContext
            The config context.

        Returns
        -------
        Any
            The loaded value.
        """
        return load(factory, value, context)


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


def no_loader_strategy(
    cls: type[ConfigT],
    value: Any,
    _context: BaseConfigContext[ConfigT],
) -> Any:
    if isinstance(value, cls):
        return value
    return cls(value)


loaders = functools.singledispatch(no_loader_strategy)


def load(
    factory: Callable[[Any], Any],
    value: Any,
    context: BaseConfigContext[ConfigT],
) -> Any:
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


def converter(func: Callable[[Any], dict[str, Any]], obj: Any = _MISSING) -> Any:
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


def loader(
    func: Callable[[type[ConfigT], Mapping[str, Any], BaseConfigContext[ConfigT]], Any],
    factory: Any = _MISSING,
) -> Any:
    """Register a loader for an object within a convenient decorator.

    Parameters
    ----------
    func : Callable[[Any], Any]
        The loader function.

    factory : Any
        The factory to register the loader for.

    Returns
    -------
    Any
        The factory function.

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
