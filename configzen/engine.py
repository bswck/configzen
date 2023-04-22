import functools
import importlib
from collections.abc import ByteString, MutableMapping
from typing import Any, ClassVar


class Engine:
    name: ClassVar[str]
    _registry = {}

    def load(
        self,
        blob: str | ByteString,
        defaults: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any]:
        """
        Load a config from a blob.

        Parameters
        ----------
        blob : str | ByteString
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
        autoconvert: bool = True
    ):
        """
        Dump a config to a blob.

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
        Engine._registry[name] = cls

    @functools.singledispatchmethod
    def convert(self, obj: Any) -> Any:
        """
        Engine-specific conversion of config values.

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
        """
        Engine-specific loading of config values.

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
    """
    Default conversion of config objects.

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


loaders = functools.singledispatch(lambda: None)


def load(factory: Any, value: Any) -> Any:
    """
    Default loading of config objects.

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
    return loaders.dispatch(factory)(factory, value)


def converter(func):
    """
    Register a converter for an object within a convenient decorator.

    Parameters
    ----------
    func : Callable[[Any], Any]
        The converter function.

    Returns
    -------
    Callable[[Any], Any]
        The converter function.
    """
    def wrapper(obj) -> Any:
        convert.register(obj, func)
        return obj
    return wrapper


def loader(func):
    """
    Register a loader for an object within a convenient decorator.

    Parameters
    ----------
    func : Callable[[Any], Any]
        The loader function.

    Returns
    -------
    Callable[[Any], Any]
        The loader function.

    """
    def wrapper(factory) -> Any:
        loaders.register(factory, func)
        return factory
    return wrapper


def get_engine_class(engine_name: str) -> type[Engine]:
    """
    Get the engine class for the given engine name.

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
    if engine_name not in Engine._registry:
        try:
            importlib.import_module(f'configzen.engines.{engine_name}_engine')
        except ImportError:
            pass
    return Engine._registry[engine_name]
