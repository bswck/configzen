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
import dataclasses
import inspect
import pathlib
import sys
import types
import contextlib
import io
from typing import (
    Callable, TYPE_CHECKING, Any, NamedTuple, TextIO, TypeVar, cast, ClassVar, Generic
)
from urllib.parse import urlparse, uses_netloc, uses_params, uses_relative
from urllib.request import urlopen

from configzen.engine import Engine, convert, get_engine_class, load, loaders
from configzen.section import dataclass_load


if sys.version_info >= (3, 11):
    from typing import dataclass_transform
else:
    from typing_extensions import dataclass_transform


if TYPE_CHECKING:
    from collections.abc import ByteString, MutableMapping, Mapping

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


__all__ = (
    'ConfigSpec',
    'BaseConfig',
    'Config',
    'AsyncConfig',
    'BaseLoader',
    'DefaultLoader'
)


_URL_SCHEMES = set(uses_relative + uses_netloc + uses_params) - {""}

ConfigType = TypeVar("ConfigType", bound="BaseConfig")  # Y001
Opened = contextlib.AbstractContextManager[io.StringIO | io.BytesIO | TextIO]
SectionConfig = TypeVar("SectionConfig", "Config", "AsyncConfig")


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
        engine_name: str,
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
    def from_str(cls, spec: str, **kwargs: Any) -> ConfigSpec:
        """Create a ConfigSpec from a string."""
        engine_name = pathlib.Path(spec).suffix[1:]
        return cls(spec, engine_name, **kwargs)

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
            return io.StringIO()
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
        return aiofiles.open(cast(str, self.filepath_or_stream), **kwds)

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


class BaseLoader:
    """A strategy for loading a configuration to a dictionary of objects.

    Parameters
    ----------
    sections : dict, optional
        A dictionary of configuration keys and their corresponding types.
        If not provided, the sections must be provided as keyword arguments.
    """
    sections: dict[str, Callable]

    def __init__(self, sections: dict[str, Callable] | None = None) -> None:
        self.sections = sections or {}

    @classmethod
    def with_sections(cls, sections) -> BaseLoader:
        return cls(sections=sections)

    async def load_async(
        self,
        data: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError

    def load(
        self,
        data: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError


if TYPE_CHECKING:
    class ConfigSection(NamedTuple, Generic[ConfigType]):
        config: ConfigType
        key: str

else:
    class ConfigSection(NamedTuple):
        """Metadata for a configuration item."""
    
        config: ConfigType
        key: str


class DefaultLoader(BaseLoader):
    def __init__(
        self,
        sections: dict[str, Callable] | None = None,
        *,
        strict: bool = False,
    ) -> None:
        super().__init__(sections)
        self.strict = strict
        self._deferred_items: dict[str, Any] = {}

    @classmethod
    def strict_with_sections(cls, sections) -> DefaultLoader:
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

    def load(self, data):
        data = {
            key: self._load_item(key, value)
            for key, value in data.items()
        }
        return data

    async def load_async(self, data):
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
    blob = config._context.spec.engine.dump(convert_config(data))
    result = config.write(blob)
    config._context.original = types.MappingProxyType(data)
    return result


async def save_async(section: AsyncConfig | ConfigSection):
    if isinstance(section, AsyncConfig):
        config = section
        return await config.save_async()

    config = section.config
    data = dict(config.original)
    data.update({section.key: config[section.key]})
    blob = config._context.spec.engine.dump(convert_config(data))
    result = await config.write_async(blob)
    config._context.original = types.MappingProxyType(data)
    return result


@dataclasses.dataclass(slots=True)
class ConfigContext:
    spec: ConfigSpec
    original: types.MappingProxyType = types.MappingProxyType({})
    loaded: bool = False


@dataclass_transform()
class BaseConfig:
    """A configuration dictionary.

    Notes
    -----
    Either ``loader`` or ``sections`` must be provided.
    If both are provided, a ValueError is raised.
    """

    _loader: ClassVar[BaseLoader]
    _context: ConfigContext
    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field]]

    @abc.abstractmethod
    def __call__(self, **config: Any) -> Any:
        """Update the configuration with the given keyword arguments."""

    def __getitem__(self, item):
        return self.as_dict()[item]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def was_loaded(self) -> bool:
        """
        Whether the configuration has been loaded.
        
        Returns
        -------
        bool
        """
        return self._context.loaded

    @property
    def sections(self) -> dict[str, Any]:
        """The configuration sections."""
        return self._loader.sections

    @sections.setter
    def sections(self, value):
        """Set the configuration sections."""
        self._loader.sections = value

    @property
    def original(self) -> Mapping:
        """The original configuration dictionary."""
        return self._context.original

    def section(self: ConfigType, key: str) -> ConfigSection[ConfigType]:
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

    def update(self, data):
        for attr, value in data.items():
            setattr(self, attr, value)

    def rollback(self) -> None:
        """Rollback the configuration to its original state."""
        self._context.loaded = False
        self.update(self._context.original)
        self._context.loaded = True

    def __init_subclass__(
        cls, 
        loader_factory
        : Callable[[dict[str, type]], BaseLoader] 
        = DefaultLoader.strict_with_sections,
        root: bool = False,
    ):
        if root:
            return 
        dataclasses.dataclass(cls)
        sections = {
            field.name: field.type
            for field in dataclasses.fields(cls)
        }
        cls._loader = loader_factory(sections)
        loaders.register(cls, dataclass_load)


@dataclass_transform()
class Config(BaseConfig, root=True):

    def __call__(self: ConfigType, **config: Any) -> ConfigType:
        """Update the configuration with the given configuration.

        Parameters
        ----------
        **config : Any
            The configuration to update with.

        Returns
        -------
        self
        """
        objects = self._loader.load(config)
        self.update(objects)
        return self

    @classmethod
    def load(
        cls: type[ConfigType],
        spec: ConfigSpec | str,
        create_missing: bool = False,
        **kwargs: Any,
    ) -> ConfigType:
        """Load the configuration file.
        To reload the configuration, use the ``reload`` method.

        Parameters
        ----------
        spec : ConfigSpec
            The configuration specification.
        create_missing : bool
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if isinstance(spec, str):
            spec = ConfigSpec.from_str(spec)
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        config = load(cls, cls._loader.load(spec.read(**kwargs)))
        config._context = ConfigContext(spec)
        return config

    def reload(self: ConfigType, **kwargs: Any) -> ConfigType:
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
        if not self._context.loaded:
            raise ValueError("Configuration has not been loaded, use load() instead")
        self._context.loaded = False
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        new_config = self._context.spec.read(**kwargs)
        self._context.original = types.MappingProxyType(new_config)
        config = self(**copy.deepcopy(new_config))
        self._context.loaded = True
        return config

    def save(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        data = self.as_dict()
        blob = self._context.spec.engine.dump(data)
        result = self.write(blob, **kwargs)
        self._context.original = types.MappingProxyType(data)
        return result

    def write(self, blob: str | ByteString, **kwargs) -> int:
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
        if self._context.spec.is_url:
            raise NotImplementedError("Saving to URLs is not yet supported")
        kwargs.setdefault("mode", "w")
        return self._context.spec.write(blob, **kwargs)


@dataclass_transform()
class AsyncConfig(BaseConfig, root=True):

    async def __call__(self: ConfigType, **config: Any) -> ConfigType:
        """Update the configuration with the given configuration, asynchronously.

        Parameters
        ----------
        config

        Returns
        -------

        """
        objects = await self._loader.load_async(config)
        self.update(objects)
        return self

    @classmethod
    async def load_async(
        cls: type[ConfigType],
        spec: ConfigSpec | str,
        create_missing: bool = False,
        **kwargs: Any,
    ) -> ConfigType:
        """Load the configuration file asynchronously.
        To reload the configuration, use the ``reload`` method.

        Parameters
        ----------
        spec : ConfigSpec
            The configuration specification.
        create_missing : bool
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        if isinstance(spec, str):
            spec = ConfigSpec.from_str(spec)
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        config = load(cls, await cls._loader.load_async(spec.read(**kwargs)))
        config._context = ConfigContext(spec)
        return config

    async def reload_async(self: ConfigType, **kwargs) -> ConfigType:
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
        if not self._context.loaded:
            raise ValueError("Configuration has not been loaded, use load() instead")
        self._context.loaded = False
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        new_async_config = await self._context.spec.read_async(**kwargs)
        self._context.original = types.MappingProxyType(new_async_config)
        async_config = await self(**copy.deepcopy(new_async_config))
        self._context.loaded = True
        return async_config

    async def save_async(self, **kwargs) -> int:
        """Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        data = self.as_dict()
        blob = self._context.spec.engine.dump(data)
        result = await self.write_async(blob, **kwargs)
        self._context.original = types.MappingProxyType(data)
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
        if self._context.spec.is_url:
            raise NotImplementedError("Saving to URLs is not yet supported")
        kwargs.setdefault("mode", "w")
        return await self._context.spec.write_async(blob, **kwargs)


@convert.register(Config)
@convert.register(AsyncConfig)
def convert_config(config: MutableMapping[str, Any]) -> dict[str, Any]:
    return {key: convert(value) for key, value in config.items()}
