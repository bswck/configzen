"""Configuration objects and utilities.

This module provides the :class:`ConfigSpec` class, which is used to
specify a configuration file and its options, and the :class:`BaseConfig`
class, which is used to load, save, and manipulate configuration files.

.. note::

    The :class:`BaseConfig` class is not meant to be used directly.
    Instead, use the :class:`Config` or :class:`AsyncConfig` classes.

    The :class:`Config` class is a synchronous configuration class, and
    the :class:`AsyncConfig` class is an asynchronous configuration class.
"""

from __future__ import annotations

import abc
import copy
import inspect
import pathlib
import types
from collections import UserDict
from contextlib import AbstractContextManager
from io import BytesIO, StringIO
from typing import TYPE_CHECKING, Any, NamedTuple, TextIO, TypeVar, cast
from urllib.parse import urlparse, uses_netloc, uses_params, uses_relative
from urllib.request import urlopen

from configzen.engine import Engine, convert, get_engine_class, load

if TYPE_CHECKING:
    from collections.abc import ByteString, Generator, MutableMapping

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore[assignment]
    AIOFILES_AVAILABLE = False

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

_URL_SCHEMES = set(uses_relative + uses_netloc + uses_params) - {""}

ConfigSelf = TypeVar("ConfigSelf", bound="BaseConfig")  # Y001
Opened = AbstractContextManager[StringIO | BytesIO | TextIO]
T_co = TypeVar("T_co", covariant=True)


class ConfigSpec:
    """A specification for a configuration file."""
    filepath_or_stream: Opened | str
    defaults: dict[str, Any]
    create_missing: bool
    engine_name: str
    _engine: Engine | None
    _engine_options: dict[str, Any]
    
    def __init__(
        self: ConfigSpec,
        filepath_or_stream: Opened | str,
        engine_name: str | None = None,
        *,
        cache_engine: bool = True,
        defaults: dict[str, Any] | None = None,
        create_missing: bool = False,
        **engine_options: Any,
    ) -> None:
        """Parameters
        ----------
        filepath_or_stream : str or file-like object, optional
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
        create_missing : bool, optional
            Whether to automatically create missing keys when loading the configuration.
        **engine_options
            Additional keyword arguments to pass to the engine.
        """
        self.filepath_or_stream = filepath_or_stream
        self.defaults = defaults or {}

        if engine_name is None and isinstance(filepath_or_stream, str):
            # Infer engine name from file extension
            engine_name = pathlib.Path(filepath_or_stream).suffix[1:]

        if engine_name is None:
            raise ValueError("engine_name must be provided")

        self.engine_name = engine_name
        self._engine = None
        self._engine_options = engine_options
        if cache_engine:
            self._engine = get_engine_class(self.engine_name)(**engine_options)
        self.cache_engine = cache_engine

        self.create_missing = create_missing

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
        """Whether the filepath_or_stream is a URL."""
        return (
            isinstance(self.filepath_or_stream, str)
            and urlparse(self.filepath_or_stream).scheme in _URL_SCHEMES
        )

    @classmethod
    def from_str(cls, spec: str) -> ConfigSpec:
        """Create a ConfigSpec from a string."""
        return cls(spec)

    def open(self, **kwds) -> Opened:
        """Open the configuration file.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.
        """
        if self.filepath_or_stream is None:
            return StringIO()
        if self.is_url:
            url = cast(str, self.filepath_or_stream)
            return urlopen(url, **kwds)
        if isinstance(self.filepath_or_stream, str):
            return open(self.filepath_or_stream, **kwds)
        return self.filepath_or_stream

    def open_async(self, **kwds) -> Any:
        """Open the configuration file asynchronously.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.
        """
        if self.is_url:
            raise NotImplementedError("asynchronous URL opening is not supported")
        if not AIOFILES_AVAILABLE:
            raise RuntimeError(
                'aiofiles is not available, '
                'cannot open file asynchronously (install with "pip install aiofiles")',
            )
        return aiofiles.open(self.filepath_or_stream, **kwds)

    def read(self, *, create_kwds=None, **kwds) -> MutableMapping[str, Any]:
        """Read the configuration file.

        Parameters
        ----------
        create_kwds : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwds
            Keyword arguments to pass to the open method.
        """
        try:
            with self.open(**kwds) as fp:
                blob = fp.read()
        except FileNotFoundError:
            blob = None
            if self.create_missing:
                blob = self.engine.dump(convert_config(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                self.write(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    def write(self, blob, **kwds) -> int:
        with self.open(**kwds) as fp:
            return fp.write(blob)

    async def read_async(self, *, create_kwds=None, **kwds) -> MutableMapping[str, Any]:
        """Read the configuration file asynchronously.

        Parameters
        ----------
        create_kwds : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwds
            Keyword arguments to pass to the open method.
        """
        try:
            async with self.open_async(**kwds) as fp:
                blob = await fp.read()
        except FileNotFoundError:
            if self.create_missing:
                blob = self.engine.dump(convert_config(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                await self.write_async(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    async def write_async(self, blob, **kwds) -> int:
        async with self.open_async(**kwds) as fp:
            return await fp.write(blob)


class LoadingStrategy:
    """A strategy for loading a configuration to a dictionary of objects.

    Parameters
    ----------
    sections : dict, optional
        A dictionary of configuration keys and their corresponding types.
        If not provided, the sections must be provided as keyword arguments.
    """

    sections: dict[str, Any]

    def __init__(self, sections: dict[str, Any] | None = None) -> None:
        self.sections = sections or {}

    @classmethod
    def with_sections(cls, **sections: Any):
        return cls(sections=sections)

    async def load_async(
        self,
        data: dict[str, Any],
        config: BaseConfig | None = None,
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        config : BaseConfig, optional
            The configuration object. If provided, the configuration metadata
            will be bound to the loaded objects.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError

    def load(
        self,
        data: dict[str, Any],
        config: BaseConfig | None = None,
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        config : BaseConfig, optional
            The configuration object. If provided, the configuration metadata
            will be bound to the loaded objects.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError


class ConfigSection(NamedTuple):
    """Metadata for a configuration item."""

    config: BaseConfig
    key: str


class DefaultLoader(LoadingStrategy):
    def __init__(
        self,
        sections: dict[str, Any] | None = None,
        *,
        strict: bool = False,
    ) -> None:
        super().__init__(sections)
        self.strict = strict
        self._deferred_items: dict[str, Any] = {}

    @classmethod
    def strict_with_sections(cls, **sections: Any):
        return cls(sections=sections, strict=True)

    def load_deferred_items(self):
        for key, value in self._deferred_items.items():
            self._load_item(key, value)

    def _load_item(self, key, value):
        try:
            factory = self.sections[key]
        except KeyError:
            self._deferred_items.update({key: value})
            return value
        self._deferred_items.pop(key, None)
        return load(factory, value)

    async def _async_load_item(self, key, value):
        data = self._load_item(key, value)
        if inspect.isawaitable(data):
            data = await data
        return data

    def load(self, data, config=None):
        data = {
            key: self._load_item(key, value)
            for key, value in data.items()
        }
        return data

    async def load_async(self, data, config=None):
        data = {
            key: await self._async_load_item(key, value)
            for key, value in data.items()
        }
        return data


def save(section: Config | ConfigSection):
    if isinstance(section, Config):
        config = section
        return config.save()
    
    config = section.config
    data = dict(config.original)
    data.update({section.key: config[section.key]})
    blob = config.spec.engine.dump(convert_config(data))
    result = config.write(blob)
    config._original = types.MappingProxyType(data)
    return result


async def save_async(section: AsyncConfig | ConfigSection):
    if isinstance(section, AsyncConfig):
        config = section
        return await config.save_async()

    config = section.config
    data = dict(config.original)
    data.update({section.key: config[section.key]})
    blob = config.spec.engine.dump(convert_config(data))
    result = await config.write_async(blob)
    config._original = types.MappingProxyType(data)
    return result


class BaseConfig(UserDict[str, Any]):
    """A configuration dictionary.

    Parameters
    ----------
    spec : ConfigSpec | str
        A ConfigSpec instance or a string representing a ConfigSpec.
    loader : DispatchStrategy, optional
        A strategy for loading the configuration to a set of objects.
        If not provided, the sections must be provided as keyword arguments.
    create_missing : bool, optional
        Whether to create missing configuration files. Defaults to False.
    defaults : dict, optional
        Default values for the configuration. Defaults to None.
    sections : dict, optional
        A sections for the configuration. Defaults to None.
        
    Notes
    -----
    Either ``loader`` or ``sections`` must be provided.
    If both are provided, a ValueError is raised.
    """

    loader: LoadingStrategy
    spec: ConfigSpec
    lazy: bool
    _original: types.MappingProxyType[str, Any]

    def __init__(
        self: ConfigSelf,
        spec: ConfigSpec | str,
        engine_name: str | None = None,
        loader: LoadingStrategy | None = None,
        create_missing: bool | None = None,
        defaults: dict[str, Any] | None = None,
        sections: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()

        if loader:
            if sections is not None:
                raise ValueError("Cannot provide both loader and sections")

            self.loader = loader
        else:
            self.loader = DefaultLoader(sections)

        if not isinstance(spec, ConfigSpec):
            spec = ConfigSpec(spec, sections=self.sections, engine_name=engine_name)

        if create_missing is not None:
            spec.create_missing = create_missing

        if defaults is not None:
            spec.defaults.update(defaults)

        self.spec = spec

        self._original = types.MappingProxyType({})
        self._loaded = False

    @abc.abstractmethod
    def __call__(self: ConfigSelf, **config: Any) -> Any:
        """Update the configuration with the given keyword arguments."""

    @property
    def sections(self) -> dict[str, Any]:
        """The configuration sections."""
        return self.loader.sections

    @sections.setter
    def sections(self, value):
        """Set the configuration sections."""
        self.loader.sections = value

    @property
    def original(self) -> types.MappingProxyType:
        """The original configuration dictionary."""
        return self._original

    def section(self: ConfigSelf, key: str) -> ConfigSection:
        """Return the configuration section metadata.

        Parameters
        ----------
        key
            The key of the item.

        Returns
        -------
        dict
            The item metadata.
        """
        return ConfigSection(self, key)

    def rollback(self: ConfigSelf) -> None:
        """Rollback the configuration to its original state."""
        self._loaded = False
        self.clear()
        self.update(self._original)
        self._loaded = True

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {item}",
            ) from None


class Config(BaseConfig):

    def __call__(self: ConfigSelf, **config: Any) -> ConfigSelf:
        """Update the configuration with the given configuration.

        Parameters
        ----------
        **config : Any
            The configuration to update with.

        Returns
        -------
        self
        """
        objects = self.loader.load(config, self)
        self.update(objects)
        return self

    def load(self: ConfigSelf, **kwargs) -> ConfigSelf:
        """Load the configuration file.
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
        if self._loaded:
            raise ValueError("Configuration is already loaded")
        return self._load_impl(**kwargs)

    def reload(self: ConfigSelf, **kwargs) -> ConfigSelf:
        """Reload the configuration file.
        If the configuration is not loaded, a ValueError is raised.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if not self._loaded:
            raise ValueError("Configuration has not been loaded, use load() instead")
        self._loaded = False
        return self._load_impl(**kwargs)

    def _load_impl(self: ConfigSelf, **kwargs: Any) -> ConfigSelf:
        new_config = self.read(**kwargs)
        self._original = types.MappingProxyType(new_config)
        config = self(**copy.deepcopy(new_config))
        self._loaded = True
        return config

    def read(self: ConfigSelf, **kwargs: Any) -> MutableMapping[str, Any]:
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        return self.spec.read(**kwargs)

    def save(self: ConfigSelf, **kwargs: Any) -> int:
        """Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        blob = self.spec.engine.dump(self)
        result = self.write(blob, **kwargs)
        self._original = types.MappingProxyType(self.data)
        return result

    def write(self: ConfigSelf, blob: str | ByteString, **kwargs) -> int:
        """Overwrite the configuration file with the given blob
        (config dump as string or bytes).

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
            raise NotImplementedError("Saving to URLs is not yet supported")
        kwargs.setdefault("mode", "w")
        return self.spec.write(blob, **kwargs)


class AsyncConfig(BaseConfig):

    def __await__(self: ConfigSelf) -> Generator[Any, None, ConfigSelf]:
        return self.load_async().__await__()

    async def __call__(self: ConfigSelf, **config) -> ConfigSelf:
        """Update the configuration with the given configuration, asynchronously.

        Parameters
        ----------
        config

        Returns
        -------

        """
        objects = await self.loader.load_async(config, self)
        self.update(objects)
        return self

    async def load_async(self: ConfigSelf, **kwargs) -> ConfigSelf:
        """Load the configuration file.
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
        if self._loaded:
            raise ValueError("Configuration is already loaded")
        return self._async_load_impl(**kwargs)

    async def reload_async(self: ConfigSelf, **kwargs) -> ConfigSelf:
        """Reload the configuration file.
        If the configuration is not loaded, a ValueError is raised.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if not self._loaded:
            raise ValueError("Configuration has not been loaded, use load() instead")
        self._loaded = False
        return self._async_load_impl(**kwargs)

    async def _load_async_impl(self: ConfigSelf, **kwargs: Any) -> ConfigSelf:
        new_async_config = self.read_async(**kwargs)
        if inspect.isawaitable(new_async_config):
            new_async_config = await new_async_config
        self._original = types.MappingProxyType(new_async_config)
        async_config = await self(**copy.deepcopy(new_async_config))
        self._loaded = True
        return async_config

    async def read_async(self: ConfigSelf, **kwargs: Any) -> MutableMapping[str, Any]:
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        return await self.spec.read_async(**kwargs)

    async def save_async(self, **kwargs) -> int:
        """Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        blob = self.spec.engine.dump(self)
        result = await self.write_async(blob, **kwargs)
        self._original = types.MappingProxyType(self.data)
        return result

    async def write_async(self, blob: str | ByteString, **kwargs: Any) -> int:
        """Overwrite the configuration file asynchronously with the given blob
        (config dump as string or bytes).

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
            raise NotImplementedError("Saving to URLs is not yet supported")
        kwargs.setdefault("mode", "w")
        return await self.spec.write_async(blob, **kwargs)


@convert.register(Config)
@convert.register(AsyncConfig)
def convert_config(config: BaseConfig) -> dict[str, Any]:
    return {key: convert(value) for key, value in config.items()}
