"""Core configuration classes and functions."""

from __future__ import annotations

import abc
import contextlib
import copy
import dataclasses
import inspect
import io
import os
import pathlib
import sys
from collections.abc import ByteString, Callable, Generator, Iterator, MutableMapping
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    NamedTuple,
    TypeVar,
    cast
)
from urllib.parse import urlparse, uses_netloc, uses_params, uses_relative
from urllib.request import Request, urlopen

from configzen.engine import Engine, convert, get_engine_class, load, loaders
from configzen.recipes import dataclass_load

if sys.version_info >= (3, 11):
    from typing import dataclass_transform
else:
    from typing_extensions import dataclass_transform

if TYPE_CHECKING:
    from collections.abc import ItemsView, Mapping

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore[assignment]
    AIOFILES_AVAILABLE = False

__all__ = (
    "ConfigSpec",
    "BaseConfig",
    "Config",
    "AsyncConfig",
    "BaseLoader",
    "DefaultLoader",
    "save",
)

_URL_SCHEMES: set[str] = set(uses_relative + uses_netloc + uses_params) - {""}
_CONTEXT_ATTRIBUTE: str = "_context"

ContextT = TypeVar("ContextT", bound="BaseConfigContext")
if sys.version_info >= (3, 10):
    BlobT = TypeVar("BlobT", str, ByteString)
else:
    BlobT = TypeVar("BlobT", str, bytes, bytearray, memoryview)
LoaderFactoryT = Callable[[dict[str, Callable]], "BaseLoader"]
BaseConfigT = TypeVar("BaseConfigT", bound="BaseConfig")
ConfigT = TypeVar("ConfigT", bound="Config")
AsyncConfigT = TypeVar("AsyncConfigT", bound="AsyncConfig")
OpenedT = contextlib.AbstractContextManager


class ConfigSpec(Generic[BlobT]):
    """A specification for a configuration file."""

    filepath_or_stream: OpenedT | str | os.PathLike | pathlib.Path
    defaults: dict[str, Any]
    create_missing: bool
    engine_name: str
    _engine: Engine | None
    _engine_options: dict[str, Any]
    cache_engine: bool
    allowed_url_schemes: set[str] = _URL_SCHEMES

    def __init__(
        self: ConfigSpec[BlobT],
        filepath_or_stream: OpenedT[BlobT] | str,
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
        kwargs.setdefault("engine_name", pathlib.Path(spec).suffix[1:])
        return cls(spec, **kwargs)

    def open_sync(self, **kwds: Any) -> OpenedT:
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
            if urlparse(url).scheme not in self.allowed_url_schemes:
                msg = (
                    f"URL scheme {urlparse(url).scheme!r} is not allowed, "
                    f"must be one of {self.allowed_url_schemes!r}"
                )
                raise ValueError(msg)
            return urlopen(Request(url), **kwds)  # noqa: S310, ^
        if isinstance(self.filepath_or_stream, (str, os.PathLike, pathlib.Path)):
            return pathlib.Path(self.filepath_or_stream).open(**kwds)
        return self.filepath_or_stream

    def open_async(self, **kwds: Any) -> Any:
        """Open the configuration file asynchronously.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.
        """
        if self.is_url:
            msg = "asynchronous URL opening is not supported"
            raise NotImplementedError(msg)
        if not AIOFILES_AVAILABLE:
            msg = (
                "aiofiles is not available, cannot open file "
                "asynchronously (install with `pip install aiofiles`)"
            )
            raise RuntimeError(
                msg,
            )
        return aiofiles.open(cast(str, self.filepath_or_stream), **kwds)

    def read(
        self,
        *,
        create_kwds: dict[str, Any] | None = None,
        **kwds: Any,
    ) -> dict[str, Any]:
        """Read the configuration file.

        Parameters
        ----------
        create_kwds : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwds
            Keyword arguments to pass to the open method.
        """
        blob: str | ByteString | None
        try:
            with self.open_sync(**kwds) as fp:
                blob = fp.read()
        except FileNotFoundError:
            blob = None
            if self.create_missing:
                blob = self.engine.dump(config_convert(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                self.write(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    def write(self, blob: str | ByteString, **kwds: Any) -> int:
        with self.open_sync(**kwds) as fp:
            return fp.write(blob)

    async def read_async(
        self,
        *,
        create_kwds: dict[str, Any] | None = None,
        **kwds: Any,
    ) -> dict[str, Any]:
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
                blob = self.engine.dump(config_convert(self.defaults))
                if create_kwds is None:
                    create_kwds = {}
                await self.write_async(blob, **create_kwds)
        return self.engine.load(blob, defaults=self.defaults)

    async def write_async(self, blob: str | ByteString, **kwds: Any) -> int:
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

    sections: dict[str, Callable[[Any], Any]]

    def __init__(self, sections: dict[str, Callable[[Any], Any]] | None = None) -> None:
        self.sections = sections or {}

    @classmethod
    def with_sections(cls, sections: dict[str, Callable[[Any], Any]]) -> BaseLoader:
        return cls(sections=sections)

    async def load_async(
        self,
        data: MutableMapping[str, Any],
        context: ConfigContext,
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        context : ConfigContext
            The configuration context.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError

    def load(
        self,
        data: MutableMapping[str, Any],
        context: ConfigContext,
    ) -> MutableMapping[str, Any]:
        """Dispatch the configuration to a dictionary of objects.

        Parameters
        ----------
        data : dict
            The configuration data.

        context : ConfigContext
            The configuration context.

        Returns
        -------
        dict
            The ready-to-use configuration dictionary.

        """
        raise NotImplementedError


if TYPE_CHECKING:
    MutableMappingType = TypeVar("MutableMappingType", bound="MutableMapping")  # Y001


    class ConfigAt(NamedTuple, Generic[MutableMappingType]):
        config: MutableMappingType
        route: list[str]

        def get(self) -> Any:
            ...

        def update(self, _value: Any) -> None:
            ...

        async def save_async(self) -> int:
            ...

        def save(self) -> int:
            ...


else:
    class ConfigAt(NamedTuple):
        """Metadata for a configuration item."""

        config: MutableMapping
        route: list[str]

        def get(self) -> Any:
            value = self.config
            for key in self.route:
                value = value[key]
            return value

        def update(self, value: Any) -> None:
            route = list(self.route)
            if len(route) == 1:
                self.config[route[0]] = value
            else:
                key = route.pop()
                self._replace(route=route).get()[key] = value

        async def save_async(self) -> int:
            return await save_async(self)

        def save(self) -> int:
            return save(self)


class DefaultLoader(BaseLoader):
    def __init__(
        self,
        sections: dict[str, Callable[[Any], Any]] | None = None,
        *,
        strict: bool = False,
    ) -> None:
        super().__init__(sections)
        self.strict = strict
        self._deferred_items: dict[str, Any] = {}

    @classmethod
    def strict_with_sections(cls, sections: dict[str, Callable]) -> DefaultLoader:
        return cls(sections=sections, strict=True)

    def load_deferred_items(self) -> None:
        for key, (value, context) in self._deferred_items.items():
            self._load_item(key, value, context)

    def _load_item(self, key: str, value: Any, context: ContextT) -> Any:
        try:
            factory = self.sections[key]
        except KeyError:
            self._deferred_items.update({key: (value, context)})
            return value
        self._deferred_items.pop(key, None)
        return load(factory, value, context)

    async def _async_load_item(self, key: str, value: Any, context: ContextT) -> Any:
        data = self._load_item(key, value, context)
        if inspect.isawaitable(data):
            data = await data
        return data

    def load(self, data: Mapping | None, context: ContextT) -> Any:
        return {
            key: self._load_item(key, value, context.enter(key))
            for key, value in (data or {}).items()
        }

    async def load_async(self, data: Mapping | None, context: ContextT) -> Any:
        return {
            key: await self._async_load_item(key, value, context.enter(key))
            for key, value in (data or {}).items()
        }


def save(section: ConfigT | ConfigAt[ConfigT]) -> int:
    if isinstance(section, Config):
        config = section
        return config.save()

    section = cast(ConfigAt[ConfigT], section)
    config = cast(ConfigT, section.config)
    data = config.original
    at = ConfigAt(data, section.route)
    at.update(section.get())
    context = ConfigContext.get(config)
    spec = cast(ConfigSpec, context.spec)
    blob = spec.engine.dump(config_convert(data))
    result = config.write(blob)
    context.original = data
    return result


async def save_async(section: AsyncConfigT | ConfigAt[AsyncConfigT]) -> int:
    if isinstance(section, AsyncConfig):
        config = section
        return await config.save_async()

    if TYPE_CHECKING:
        section = cast(ConfigAt[AsyncConfigT], section)
    config = section.config
    data = config.original
    ConfigAt(data, section.route).update(section.get())
    context = ConfigContext.get(config)
    spec = cast(ConfigSpec, context.spec)
    blob = spec.engine.dump(config_convert(data))
    result = await config.write_async(blob)
    context.original = data
    return result


class BaseConfigContext(abc.ABC, Generic[ConfigT]):
    original: dict[str, Any]
    _original: dict[str, Any]
    loaded: bool

    @abc.abstractmethod
    def trace_route(self) -> Generator[str, None, None]:
        """Trace the route to the configuration context."""

    @classmethod
    def get(cls, config: ConfigT) -> BaseConfigContext[ConfigT]:
        return object.__getattribute__(config, _CONTEXT_ATTRIBUTE)

    def bind_to(self, config: ConfigT) -> None:
        if config is None:
            return
        object.__setattr__(config, _CONTEXT_ATTRIBUTE, self)

    def enter(self, key: str) -> ConfigSubcontext[ConfigT]:
        return ConfigSubcontext(self, key)

    @property
    @abc.abstractmethod
    def spec(self) -> ConfigSpec | None:
        ...

    @property
    @abc.abstractmethod
    def owner(self) -> ConfigT | None:
        ...

    @property
    @abc.abstractmethod
    def section(self) -> ConfigT | ConfigAt[ConfigT]:
        ...


class ConfigSubcontext(BaseConfigContext, Generic[ConfigT]):
    def __init__(self, parent: BaseConfigContext[ConfigT], key: str) -> None:
        self.parent = parent
        self.key = key

    @property
    def spec(self) -> None:
        return None  # ???

    def trace_route(self) -> Generator[str, None, None]:
        yield from self.parent.trace_route()
        yield self.key

    @property
    def section(self) -> ConfigAt[ConfigT]:
        if self.owner is None:
            msg = "Cannot get section for unbound context"
            raise ValueError(msg)
        return ConfigAt(self.owner, list(self.trace_route()))

    @property
    def owner(self) -> ConfigT | None:
        return self.parent.owner

    @property
    def original(self) -> dict[str, Any]:
        return self.parent.original

    @original.setter
    def original(self, value: dict[str, Any]) -> None:
        data = self.parent.original
        data[self.key] = value
        self.parent.original = data

    @property
    def loaded(self) -> bool:
        return self.parent.loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        self.parent.loaded = value


class ConfigContext(BaseConfigContext, Generic[BaseConfigT]):
    def __init__(self, spec: ConfigSpec, owner: BaseConfigT | None = None) -> None:
        self._spec = spec
        self._owner = None
        self._original = {}
        self._loaded = False

        self.owner = owner

    def trace_route(self) -> Generator[str, None, None]:
        yield from ()

    @property
    def spec(self) -> ConfigSpec:
        return self._spec

    @property
    def section(self) -> BaseConfigT | None:
        return self.owner

    @property
    def owner(self) -> BaseConfigT | None:
        return self._owner

    @owner.setter
    def owner(self, config: BaseConfigT | None) -> None:
        if config is None:
            return
        self.bind_to(config)
        self._owner = config

    @property
    def original(self) -> dict[str, Any]:
        return copy.deepcopy(self._original)

    @original.setter
    def original(self, original: dict[str, Any]) -> None:
        self._original = dict(original)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        self._loaded = value


FieldWatcherBase: type = type(MutableMapping)


class FieldWatcher(FieldWatcherBase):
    _loader_factory: LoaderFactoryT
    _loader: BaseLoader

    def __setattr__(self, key: str, value: Any) -> None:
        super().__setattr__(key, value)
        if key == "__dataclass_fields__":
            self._on_dataclass_fields(value)

    def _on_dataclass_fields(
        self,
        dataclass_fields: dict[str, dataclasses.Field],
    ) -> None:
        sections = {
            name: cast(Callable, field.type)
            for name, field in dataclass_fields.items()
        }
        self._loader = self._loader_factory(sections)
        loaders.register(self, config_load)
        convert.register(self, config_convert)


@dataclass_transform()
class BaseConfig(MutableMapping[str, Any], metaclass=FieldWatcher):
    """A configuration dictionary.

    Notes
    -----
    Either ``loader`` or ``sections`` must be provided.
    If both are provided, a ValueError is raised.
    """

    _loader: ClassVar[BaseLoader]
    _context: ConfigContext
    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field]]

    if TYPE_CHECKING:
        # Because PyCharm doesn't understand
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            ...

    @abc.abstractmethod
    def __call__(self, **config: Any) -> Any:
        """Update the configuration with the given keyword arguments."""

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __setitem__(self, key: str, value: Any) -> None:
        self.update({key: value})

    def __getitem__(self, item: str) -> Any:
        return self.as_dict()[item]

    def __delitem__(self, key: str) -> None:
        delattr(self, key)

    def __len__(self) -> int:
        return len(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def items(self) -> ItemsView[str, Any]:
        return self.as_dict().items()

    @classmethod
    def get_defaults(cls) -> dict[str, Any]:
        return config_convert({
            field.name: field.default
            for field in dataclasses.fields(cls)
            if field.default is not dataclasses.MISSING
        })

    @property
    def was_loaded(self) -> bool:
        """Whether the configuration has been loaded.

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
    def sections(self, value: dict[str, Any]) -> None:
        """Set the configuration sections."""
        self._loader.sections = value

    @property
    def original(self) -> dict[str, Any]:
        """The original configuration dictionary."""
        return self._context.original

    def at(
        self: BaseConfigT,
        route: str | list[str],
        *,
        parse_dotlist: bool = True,
    ) -> ConfigAt[BaseConfigT]:
        """Return the configuration section metadata.

        Parameters
        ----------
        route
            Route to the key of the item.

        parse_dotlist : bool, optional
            Whether to parse the route as a dotlist, by default True


        Returns
        -------
        ConfigAt
            The item metadata.
        """
        if isinstance(route, str):
            if parse_dotlist:
                [*route] = route.split(".")
            else:
                route = [route]
        return ConfigAt(self, route)

    def update(  # type: ignore[override]
        self,
        data: Mapping[str, Any],
        /,
        **kw_data: Any,
    ) -> None:
        """Update the configuration with the given data, without loading."""
        data = {**data, **kw_data}
        for attr, value in data.items():
            setattr(self, attr, value)

    def rollback(self) -> None:
        """Rollback the configuration to its original state."""
        self._context.loaded = False
        self.update(self._context.original)
        self._context.loaded = True

    def __init_subclass__(
        cls,
        loader_factory: LoaderFactoryT = DefaultLoader.strict_with_sections,
        *,
        root: bool = False,
        make_dataclass: bool = True,
        **dataclass_params: Any,
    ) -> None:
        if root:
            return
        cls._loader_factory = loader_factory
        if make_dataclass:
            dataclasses.dataclass(cls, **dataclass_params)  # type: ignore


@dataclass_transform()
class Config(BaseConfig, root=True):

    def __call__(self: ConfigT, **new_config: Any) -> ConfigT:
        """Update the configuration with the given configuration, with loading.

        Parameters
        ----------
        **new_config : Any
            The configuration keyword arguments to update with.

        Returns
        -------
        self
        """
        self.update(self._loader.load(new_config, self._context))
        return self

    @classmethod
    def load(
        cls: type[ConfigT],
        spec: ConfigSpec | str,
        create_missing: bool | None = None,
        **kwargs: Any,
    ) -> ConfigT:
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
            spec = ConfigSpec.from_str(
                spec,
                defaults=cls.get_defaults(),
            )
        if create_missing is not None:
            spec.create_missing = create_missing
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        loader = cls._loader
        context: ConfigContext[ConfigT] = ConfigContext(spec)
        data = loader.load(spec.read(**kwargs), context)
        config = load(cls, data, context)
        context.owner = config
        context.loaded = True
        context.original = config.as_dict()
        return config

    def reload(self: ConfigT, **kwargs: Any) -> ConfigT:
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
            msg = "Configuration has not been loaded, use load() instead"
            raise ValueError(msg)
        if self._context.owner is self:
            self._context.loaded = False
            kwargs.setdefault("mode", "r")
            kwargs.setdefault("create_kwds", {"mode": "w"})
            new_config = self._context.spec.read(**kwargs)
            self._context.original = new_config
            config = cast(ConfigT, self(**new_config))
            self._context.loaded = True
            return config
        msg = "partial reloading is not supported yet"
        raise ValueError(msg)

    def save(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        if self._context.owner is self:
            data = self.as_dict()
            blob = self._context.spec.engine.dump(data)
            result = self.write(blob, **kwargs)
            self._context.original = data
            return result
        return save(self._context.section)

    def write(self, blob: str | ByteString, **kwargs: Any) -> int:
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
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return self._context.spec.write(blob, **kwargs)


@dataclass_transform()
class AsyncConfig(BaseConfig, root=True):

    async def __call__(self: AsyncConfigT, **config: Any) -> AsyncConfigT:
        """Update the configuration with the given configuration, asynchronously.

        Parameters
        ----------
        config

        Returns
        -------

        """
        objects = await self._loader.load_async(config, self._context)
        self.update(objects)
        return self

    @classmethod
    async def load_async(
        cls: type[AsyncConfigT],
        spec: ConfigSpec | str,
        *,
        create_missing: bool = False,
        **kwargs: Any,
    ) -> AsyncConfigT:
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
            spec = ConfigSpec.from_str(spec, defaults=cls.get_defaults())
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        context: ConfigContext[AsyncConfigT] = ConfigContext(spec)
        config = load(
            cls,
            await cls._loader.load_async(spec.read(**kwargs), context),
            context,
        )
        context.owner = config
        context.loaded = True
        return config

    async def reload_async(self: AsyncConfigT, **kwargs: Any) -> AsyncConfigT:
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
            msg = "Configuration has not been loaded, use load() instead"
            raise ValueError(msg)
        self._context.loaded = False
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        new_async_config = await self._context.spec.read_async(**kwargs)
        self._context.original = new_async_config
        async_config = cast(AsyncConfigT, await self(**new_async_config))
        self._context.loaded = True
        return async_config

    async def save_async(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        if self._context.owner is self:
            data = self.as_dict()
            blob = self._context.spec.engine.dump(data)
            result = await self.write_async(blob, **kwargs)
            self._context.original = data
            return result
        return await save_async(self._context.section)

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
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return await self._context.spec.write_async(blob, **kwargs)


def config_load(
    cls: type[BaseConfigT],
    value: MutableMapping[str, Any],
    context: ConfigContext[BaseConfigT],
) -> BaseConfigT:
    value = cls._loader.load(value, context)
    config = dataclass_load(cls, value, context)
    context.bind_to(config)
    return config


def config_convert(config: MutableMapping[str, Any]) -> dict[str, Any]:
    return {key: convert(value) for key, value in config.items()}
