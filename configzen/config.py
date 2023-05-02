from __future__ import annotations
import asyncio
import collections.abc
import copy
import inspect
import os.path
import threading
import types
import typing
from collections import UserDict
from collections.abc import ByteString, Coroutine, MutableMapping
from io import StringIO
from typing import Any, Protocol, TypeVar, runtime_checkable, Union
from urllib.parse import uses_relative, uses_netloc, uses_params, urlparse
from urllib.request import urlopen

from configzen.engine import convert, load, get_engine_class, Engine
from configzen.errors import ConfigError

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore
    AIOFILES_AVAILABLE = False

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore
    AIOHTTP_AVAILABLE = False


_URL_SCHEMES = set(uses_relative + uses_netloc + uses_params) - {''}


T = TypeVar('T')
DispatchReturnType = Union[MutableMapping[str, Any], Coroutine[MutableMapping[str, Any]]]


@runtime_checkable
class Readable(Protocol[T]):
    """A protocol for objects that can be read."""

    def read(self) -> T | Coroutine[T]:
        ...


class ConfigSpec:
    """
    A specification for a configuration file.

    Parameters
    ----------
    filepath_or_buffer : str or file-like object, optional
        The path to the configuration file, or a file-like object.
        If not provided, an empty configuration will be created.
    engine_name : str, optional
        The name of the engine to use for loading and saving the configuration.
        Defaults to 'yaml'.
    cache_engine : bool, optional
        Whether to cache the engine instance. Defaults to True.
        If False, a new engine instance will be created for each load and dump.
    defaults : dict, optional
        A dictionary of default values to use when loading the configuration.
    autocreate : bool, optional
        Whether to automatically create missing keys when loading the configuration.
    **engine_options
        Additional keyword arguments to pass to the engine.
    """

    def __init__(
        self,
        filepath_or_buffer: Readable | str = None,
        engine_name: str | None = None,
        cache_engine: bool = True,
        defaults: dict[str, Any] | None = None,
        autocreate: bool = False,
        **engine_options: Any,
    ):
        self.filepath_or_buffer = filepath_or_buffer
        self.defaults = defaults

        if engine_name is None:
            if isinstance(filepath_or_buffer, str):
                # Infer engine name from file extension
                engine_name = os.path.splitext(filepath_or_buffer)[1][1:]

        if engine_name is None:
            raise ValueError('engine_name must be provided')

        self.engine_name = engine_name
        self._engine = None
        self._engine_options = engine_options
        if cache_engine:
            self._engine = get_engine_class(self.engine_name)(**engine_options)
        self.cache_engine = cache_engine

        self.missing_autocreate = autocreate

    def _get_engine(self) -> Engine:
        """Get the engine instance to use for loading and saving the configuration."""
        engine = self._engine
        if engine is None:
            engine_class = get_engine_class(self.engine_name)
            engine = engine_class(**self._engine_options)
        if self.cache_engine:
            self._engine = engine
        return engine

    @property
    def engine(self) -> Engine:
        """The engine instance to use for loading and saving the configuration."""
        return self._get_engine()

    @property
    def is_url(self) -> bool:
        """Whether the filepath_or_buffer is a URL."""
        return (
            isinstance(self.filepath_or_buffer, str)
            and urlparse(self.filepath_or_buffer).scheme in _URL_SCHEMES
        )

    @classmethod
    def from_str(cls, spec: str) -> 'ConfigSpec':
        """Create a ConfigSpec from a string."""
        return cls(spec)

    def open(
        self, asynchronous: bool = False, **kwds: Any
    ) -> Readable[str | ByteString] | Coroutine[Readable[str | ByteString]]:
        """
        Open the configuration file.

        Parameters
        ----------
        asynchronous : bool, optional
            Whether to open the file asynchronously. Defaults to False.

        **kwds
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.
        """
        if asynchronous:
            return self._async_open(**kwds)
        return self._open(**kwds)

    def _async_open(self, **kwds) -> Readable[str | ByteString]:
        if self.is_url:
            raise NotImplementedError('asynchronous URL opening is not supported')
        if not AIOFILES_AVAILABLE:
            raise RuntimeError(
                'aiofiles is not available, '
                'cannot open file asynchronously (install with "pip install aiofiles")'
            )
        return aiofiles.open(self.filepath_or_buffer, **kwds)  # type: ignore

    def _open(self, **kwds) -> Readable[str | ByteString]:
        if self.filepath_or_buffer is None:
            return StringIO()
        if self.is_url:
            return urlopen(self.filepath_or_buffer, **kwds)
        return open(self.filepath_or_buffer, **kwds)

    def read(self, asynchronous: bool = False, **kwds) -> DispatchReturnType:
        """
        Read the configuration file.

        Parameters
        ----------
        asynchronous : bool, optional
            Whether to read the file asynchronously. Defaults to False.
        **kwds
            Keyword arguments to pass to the open method.
        """

        if asynchronous:
            return self._async_read(**kwds)
        return self._read(**kwds)

    def _read(self, *, create_kwds=None, **kwds) -> MutableMapping[str, Any]:
        try:
            with self.open(asynchronous=False, **kwds) as fp:
                blob = fp.read()
        except FileNotFoundError:
            if self.missing_autocreate:
                blob = self.engine.dump(convert_config(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                self._write(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    def _write(self, blob, **kwds) -> int:
        with self.open(asynchronous=False, **kwds) as fp:
            return fp.write(blob)

    async def _async_read(self, *, create_kwds=None, **kwds) -> MutableMapping[str, Any]:
        try:
            async with self.open(asynchronous=True, **kwds) as fp:
                blob = await fp.read()
        except FileNotFoundError:
            if self.missing_autocreate:
                blob = self.engine.dump(convert_config(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                await self._async_write(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    async def _async_write(self, blob, **kwds) -> int:
        async with self.open(asynchronous=True, **kwds) as fp:
            return await fp.write(blob)


class DispatchStrategy:
    """
    A strategy for dispatching a configuration to a dictionary of objects.

    Parameters
    ----------
    schema : dict, optional
        A dictionary of configuration keys and their corresponding types.
        If not provided, the schema must be provided as keyword arguments.
    **schema_kwds
        Keyword arguments corresponding to the schema.
    """

    def __init__(self, schema: dict[str, Any] | None = None, /, **schema_kwds: Any):
        if schema and schema_kwds:
            raise ValueError('Must provide either schema or schema_kwds')
        self.schema = schema or schema_kwds
        self.asynchronous = False

    async def _async_dispatch(
        self,
        data: dict[str, Any],
        config: 'Config | None' = None
    ) -> Coroutine[dict[str, Any]]:
        raise NotImplementedError

    def _dispatch(self, data: dict[str, Any], config: 'Config | None' = None) -> dict[str, Any]:
        raise NotImplementedError

    def dispatch(self, data: dict[str, Any], config: 'Config | None' = None) -> DispatchReturnType:
        """
        Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        config : Config, optional
            The configuration object. If provided, the configuration metadata
            will be bound to the dispatched objects.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        if self.asynchronous:
            return self._async_dispatch(data, config)
        return self._dispatch(data, config)


class ConfigMeta(typing.NamedTuple):
    """Metadata for a configuration item."""

    config: Config
    key: str


class SimpleDispatcher(DispatchStrategy):
    def _load_item(self, key, value):
        try:
            factory = self.schema[key]
        except KeyError:
            raise ConfigError(f'section {key!r} is undefined') from None
        return load(factory, value)

    async def _async_load_item(self, key, value):
        data = self._load_item(key, value)
        if inspect.isawaitable(data):
            data = await data
        return data

    @staticmethod
    def bind_config_meta(item, config_meta: ConfigMeta):
        try:
            item.__config_meta__ = config_meta
        except AttributeError:
            pass
        return item

    def _dispatch(self, data, config=None):
        return {
            key: self.bind_config_meta(
                item=self._load_item(key, value),
                config_meta=ConfigMeta(config, key) if config is not None else None
            )
            for key, value in data.items()
        }

    async def _async_dispatch(self, data, config=None):
        return {
            key: self.bind_config_meta(
                item=await self._async_load_item(key, value),
                config_meta=ConfigMeta(config, key) if config is not None else None
            )
            for key, value in data.items()
        }


def get_config_meta(item):
    meta = None
    if isinstance(item, ConfigMeta):
        meta = item
    if hasattr(item, '__config_meta__'):
        meta = item.__config_meta__
    return meta


def save(item):
    if isinstance(item, Config):
        return item.save()

    config_meta = get_config_meta(item)
    if isinstance(item, ConfigMeta):
        item = item.config[item.key]

    if config_meta is not None:
        config = config_meta.config
        data = dict(config.original)
        data.update({config_meta.key: item})
        blob = config.spec.engine.dump(convert_config(data))

        if config.asynchronous:
            async def async_save():
                async_result = await config.write(blob)  # type: ignore
                config._original = types.MappingProxyType(data)
                return async_result
            return async_save()

        result = config.write(blob)
        config._original = types.MappingProxyType(data)
        return result

    raise ConfigError(f'cannot save {item!r} without config metadata')


class Config(UserDict[str, Any]):
    """
    A configuration dictionary.

    Parameters
    ----------
    spec : ConfigSpec | str
        A ConfigSpec instance or a string representing a ConfigSpec.
    dispatcher : DispatchStrategy, optional
        A strategy for dispatching the configuration to a set of objects.
        If not provided, the schema must be provided as keyword arguments.
    lazy : bool, optional
        Whether to load the configuration lazily.
        If False, the configuration is loaded immediately.
    asynchronous : bool, optional
        Whether to load the configuration asynchronously.
        If None, the value is inherited from the dispatcher.
    **schema
        Keyword arguments corresponding to the schema.

    Notes
    -----
    Either ``dispatcher`` or ``**schema`` must be provided.
    If both are provided, a ValueError is raised.
    """

    dispatcher: DispatchStrategy
    schema: dict[str, Any]
    spec: ConfigSpec
    lazy: bool

    def __init__(
        self,
        spec: ConfigSpec | str,
        engine_name: str | None = None,
        dispatcher: DispatchStrategy | None = None,
        lazy: bool | None = None,
        asynchronous: bool | None = None,
        create_if_missing: bool | None = None,
        **schema: Any,
    ):
        super().__init__()

        if dispatcher:
            if schema:
                raise ValueError('Cannot provide both dispatcher and schema')

            self.dispatcher = dispatcher
            self.schema = dispatcher.schema
        else:
            self.dispatcher = SimpleDispatcher(schema)
            self.schema = schema

        if not isinstance(spec, ConfigSpec):
            spec = ConfigSpec(spec, schema=self.schema, engine_name=engine_name)

        self.spec = spec
        if create_if_missing is not None:
            spec.missing_autocreate = create_if_missing

        self.asynchronous = asynchronous
        if lazy is None:
            lazy = self.asynchronous

        if asynchronous and not lazy:
            raise ValueError('Cannot be asynchronous and not lazy')

        self.lazy = lazy
        self._original = types.MappingProxyType({})

        if not asynchronous and not lazy:
            self.load()

    def __await__(self):
        if not self.asynchronous:
            raise TypeError('Config is not asynchronous')
        return self.load().__await__()

    def __call__(self, **config):
        objects = self.dispatcher.dispatch(config, self)  # type: DispatchReturnType

        if self.asynchronous:

            async def async_update():
                nonlocal objects
                if inspect.isawaitable(objects):
                    objects = await objects
                self.update(objects)
                return self

            return async_update()

        self.update(objects)
        return self

    @property
    def asynchronous(self) -> bool:
        """Whether the configuration is asynchronous."""
        return self.dispatcher.asynchronous

    @asynchronous.setter
    def asynchronous(self, value: bool):
        if value is not None:
            self.dispatcher.asynchronous = value
        if self.asynchronous:
            self._loaded = asyncio.Event()
        else:
            self._loaded = threading.Event()

    @property
    def original(self) -> types.MappingProxyType:
        """The original configuration dictionary."""
        return self._original

    def wait_until_loaded(self):
        """Wait until the configuration is loaded."""
        self._loaded.wait()

    def load(self, **kwargs) -> 'Config | Coroutine[Config]':
        """
        Load the configuration file.
        If the configuration is already loaded, a ValueError is raised.
        To reload the configuration, use the ``reload`` method.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if self._loaded.is_set():
            raise ValueError('Configuration is already loaded')
        return self._load(**kwargs)

    def reload(self, **kwargs) -> 'Config | Coroutine[Config]':
        """
        Reload the configuration file.
        If the configuration is not loaded, a ValueError is raised.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if not self._loaded.is_set():
            raise ValueError('Configuration has not been loaded, use load() instead')
        self._loaded.clear()
        return self._load(**kwargs)

    def _load(self, **kwargs):
        if self.asynchronous:

            async def async_read():
                new_async_config = self._async_read(**kwargs)
                if inspect.isawaitable(new_async_config):
                    new_async_config = await new_async_config
                self._original = types.MappingProxyType(new_async_config)
                async_config = await self(**copy.deepcopy(new_async_config))
                self._loaded.set()
                return async_config

            return async_read()

        new_config = self._read(**kwargs)
        self._original = types.MappingProxyType(new_config)
        config = self(**copy.deepcopy(new_config))
        self._loaded.set()
        return config

    async def _async_read(self, **kwargs: Any):
        kwargs.setdefault('mode', 'r')
        kwargs.setdefault('create_kwds', {'mode': 'w'})
        return await self.spec.read(asynchronous=True, **kwargs)

    def _read(self, **kwargs: Any):
        kwargs.setdefault('mode', 'r')
        kwargs.setdefault('create_kwds', {'mode': 'w'})
        return self.spec.read(asynchronous=False, **kwargs)

    def rollback(self):
        """Rollback the configuration to its original state."""
        self._loaded.clear()
        self.clear()
        self.update(self._original)
        self._loaded.set()

    def meta(self, key: str) -> ConfigMeta:
        """
        Return the configuration item metadata.

        Parameters
        ----------
        key
            The key of the item.

        Returns
        -------
        dict
            The item metadata.
        """
        return ConfigMeta(self, key)

    def save(self, **kwargs: Any) -> int | Coroutine[int]:
        """
        Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        blob = self.spec.engine.dump(self)
        result = self.write(blob, **kwargs)
        if self.asynchronous:
            async def async_save():
                async_result = result
                if inspect.isawaitable(result):
                    async_result = await result  # type: ignore
                self._original = types.MappingProxyType(self.data)
                return async_result

            return async_save()
        self._original = types.MappingProxyType(self.data)
        return result

    def write(self, blob: str | ByteString, **kwargs) -> int | Coroutine[int]:
        """
        Overwrite the configuration file with the given blob (config dump as string or bytes).

        Parameters
        ----------
        blob : str | bytes
            The blob to write to the configuration file.
        **kwargs
            Keyword arguments to pass to the open method.

        Returns
        -------
        int
            The number of bytes written.
        """
        if self.spec.is_url:
            raise NotImplementedError(
                'Saving to URLs is not yet supported'
            )  # todo(bswck)
        kwargs.setdefault('mode', 'w')
        if self.asynchronous:
            return self._async_write(blob, **kwargs)
        return self._write(blob, **kwargs)

    async def _async_write(self, blob: str | ByteString, **kwargs: Any) -> int:
        return await self.spec._async_write(blob, **kwargs)

    def _write(self, blob: str | ByteString, **kwargs: Any) -> int:
        return self.spec._write(blob, **kwargs)

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(
                f'{type(self).__name__!r} object has no attribute {item}'
            ) from None


@convert.register(Config)
def convert_config(config: collections.abc.MutableMapping):
    return {key: convert(value) for key, value in config.items()}
