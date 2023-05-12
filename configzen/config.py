"""Core configuration classes and functions."""

from __future__ import annotations

import abc
import contextlib
import copy
import io
import os
import pathlib
import sys
from collections.abc import (
    ByteString, 
    Generator, 
    MutableMapping
)
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    NamedTuple,
    TypeVar,
    cast
)
from urllib.parse import urlparse, uses_netloc, uses_params, uses_relative
from urllib.request import Request, urlopen

from pydantic import BaseModel
from pydantic.main import ModelMetaclass

from configzen.engine import Engine, get_engine_class

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore[assignment]
    AIOFILES_AVAILABLE = False

__all__ = (
    "ConfigSpec",
    "BaseConfiguration",
    "Configuration",
    "AsyncConfiguration",
    "save",
)

_URL_SCHEMES: set[str] = set(uses_relative + uses_netloc + uses_params) - {""}
_CONTEXT_ATTRIBUTE: str = "__context__"

T = TypeVar("T")
ContextT = TypeVar("ContextT", bound="AnyContext")
if sys.version_info >= (3, 10):
    BlobT = TypeVar("BlobT", str, ByteString)
else:
    BlobT = TypeVar("BlobT", str, bytes, bytearray, memoryview)
BaseConfigT = TypeVar("BaseConfigT", bound="BaseConfiguration")
ConfigT = TypeVar("ConfigT", bound="Configuration")
AsyncConfigT = TypeVar("AsyncConfigT", bound="AsyncConfiguration")
OpenedT = contextlib.AbstractContextManager


def _get_defaults_from_model_class(model: type[BaseModel]) -> dict[str, Any]:
    defaults = {}
    for field in model.__fields__.values():
        if not field.required:
            defaults[field.name] = field.default
    return defaults


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
        create_missing : bool, optional
            Whether to automatically create missing keys when loading the configuration.
        **engine_options
            Additional keyword arguments to pass to the engine.
        """
        self.filepath_or_stream = filepath_or_stream

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

    def open_file(self, **kwds: Any) -> OpenedT:
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

    def open_file_async(self, **kwds: Any) -> Any:
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
        config_class: type[BaseConfigT],
        create_kwds: dict[str, Any] | None = None,
        **kwds: Any,
    ) -> BaseConfigT:
        """Read the configuration file.

        Parameters
        ----------
        config_class
        create_kwds : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwds
            Keyword arguments to pass to the open method.
        """
        blob: str | ByteString | None
        try:
            with self.open_file(**kwds) as fp:
                blob = fp.read()
        except FileNotFoundError:
            blob = None
            if self.create_missing:
                defaults = _get_defaults_from_model_class(config_class)
                blob = self.engine.dump_mapping(defaults)
                if create_kwds is None:
                    create_kwds = {}
                self.write(blob, **create_kwds)
        return self.engine.load(model_class=config_class, blob=blob)

    def write(self, blob: str | ByteString, **kwds: Any) -> int:
        with self.open_file(**kwds) as fp:
            return fp.write(blob)

    async def read_async(
        self,
        *,
        config_class: type[ConfigT],
        create_kwds: dict[str, Any] | None = None,
        **kwds: Any,
    ) -> ConfigT:
        """Read the configuration file asynchronously.

        Parameters
        ----------
        config_class
        create_kwds : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwds
            Keyword arguments to pass to the open method.
        """
        try:
            async with self.open_file_async(**kwds) as fp:
                blob = await fp.read()
        except FileNotFoundError:
            if self.create_missing:
                blob = self.engine.dump_mapping(self.defaults)
                if create_kwds is None:
                    create_kwds = {}
                await self.write_async(blob, **create_kwds)
        return self.engine.load(model_class=config_class, blob=blob)

    async def write_async(self, blob: str | ByteString, **kwds: Any) -> int:
        async with self.open_file_async(**kwds) as fp:
            return await fp.write(blob)


if TYPE_CHECKING:

    class ConfigAt(NamedTuple, Generic[BaseConfigT]):
        owner: BaseConfigT
        mapping: dict[str, Any] | None
        route: list[str]

        def get(self) -> Any:
            ...

        def update(self, _value: Any) -> dict[str, Any]:
            ...

        async def save_async(self) -> int:
            ...

        def save(self) -> int:
            ...


else:
    class ConfigAt(NamedTuple):
        """Metadata for a configuration item."""
        owner: BaseConfigT
        mapping: dict[str, Any] | None
        route: list[str]

        def get(self) -> Any:
            scope = self.mapping or self.owner.dict()
            for key in self.route:
                if not isinstance(scope, MutableMapping):
                    scope = scope.__dict__                
                scope = scope[key]
            return scope

        def update(self, value: Any) -> MutableMapping:
            route = list(self.route)
            mapping = self.mapping or self.owner.dict()
            key = route.pop()
            submapping = mapping
            for part in route:
                if not isinstance(submapping, MutableMapping):
                    submapping = submapping.__dict__                
                submapping = submapping[part]
            else:
                submapping[key] = value
            return mapping

        async def save_async(self) -> int:
            return await save_async(self)

        def save(self) -> int:
            return save(self)


def save(section: ConfigT | ConfigAt) -> int:
    if isinstance(section, Configuration):
        config = section
        return config.save()

    config = section.owner
    data = config.original
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = AnyContext.get(config)
    blob = context.spec.engine.dump_mapping(data)
    result = config.write(blob)
    context.original = data
    return result


async def save_async(section: AsyncConfigT | ConfigAt) -> int:
    if isinstance(section, AsyncConfiguration):
        config = section
        return await config.save_async()

    config = section.owner
    data = config.original
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = AnyContext.get(config)
    blob = context.spec.engine.dump_mapping(data)
    result = await config.write_async(blob)
    context.original = data
    return result


class AnyContext(abc.ABC, Generic[BaseConfigT]):
    original: dict[str, Any]
    _original: dict[str, Any]
    loaded: bool

    @abc.abstractmethod
    def trace_route(self) -> Generator[str, None, None]:
        """Trace the route to the configuration context."""

    @staticmethod
    def get(config: BaseConfigT) -> AnyContext[BaseConfigT]:
        return object.__getattribute__(config, _CONTEXT_ATTRIBUTE)
    
    def bind_to(self, config: BaseConfigT) -> None:
        if config is None:
            return
        object.__setattr__(config, _CONTEXT_ATTRIBUTE, self)

    def enter(self, key: str) -> Subcontext[BaseConfigT]:
        return Subcontext(self, key)

    @property
    @abc.abstractmethod
    def spec(self) -> ConfigSpec:
        ...

    @property
    @abc.abstractmethod
    def owner(self) -> BaseConfigT | None:
        ...

    @property
    @abc.abstractmethod
    def section(self) -> BaseConfigT | ConfigAt[BaseConfigT]:
        ...


class Subcontext(AnyContext, Generic[BaseConfigT]):
    def __init__(self, parent: AnyContext[BaseConfigT], key: str) -> None:
        self.parent = parent
        self.key = key

    @property
    def spec(self) -> ConfigSpec:
        # I know, raising properties are bad, but it's the only way
        raise ValueError("Cannot get spec for unbound context")
        
    def trace_route(self) -> Generator[str, None, None]:
        yield from self.parent.trace_route()
        yield self.key

    @property
    def section(self) -> ConfigAt[BaseConfigT]:
        if self.owner is None:
            msg = "Cannot get section for unbound context"
            raise ValueError(msg)
        return ConfigAt(self.owner, None, list(self.trace_route()))

    @property
    def owner(self) -> BaseConfigT | None:
        return self.parent.owner

    @property
    def original(self) -> dict[str, Any]:
        return self.parent.original

    @original.setter
    def original(self, value: dict[str, Any]) -> None:
        data = self.parent.original
        data[self.key] = copy.deepcopy(value)
        self.parent.original = data

    @property
    def loaded(self) -> bool:
        return self.parent.loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        self.parent.loaded = value


class Context(AnyContext, Generic[BaseConfigT]):
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
        self._original = original

    @property
    def loaded(self) -> bool:
        return self._loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        self._loaded = value


def get_context(config: BaseConfigT) -> AnyContext[BaseConfigT]:
    context = AnyContext.get(config)
    if context is None:
        msg = "Cannot get context for unbound configuration"
        raise ValueError(msg)
    return context


class BaseConfigZenMetaclass(ModelMetaclass):
    def __new__(mcs, name, bases, namespace, **kwargs):
        if kwargs.pop("root", None):
            return type.__new__(mcs, name, bases, namespace, **kwargs)
        namespace.setdefault("__exclude_fields__", {})[_CONTEXT_ATTRIBUTE] = True
        return super().__new__(mcs, name, bases, namespace, **kwargs)        


class BaseConfiguration(BaseModel, metaclass=BaseConfigZenMetaclass, root=True):
    """A configuration dictionary."""
    
    @property
    def was_loaded(self) -> bool:
        """Whether the configuration has been loaded.

        Returns
        -------
        bool
        """
        return get_context(self).loaded

    @property
    def original(self) -> dict[str, Any]:
        """The original configuration dictionary."""
        return get_context(self).original

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
        return ConfigAt(self, None, route)

    def rollback(self) -> None:
        """Rollback the configuration to its original state."""
        context = get_context(self)
        context.loaded = False
        self.__setstate__(get_context(self).original)
        context.loaded = True

    def _ensure_bound(self, name, value):
        context = get_context(self)
        if (
            context 
            # pydantic.BaseModel.__instancecheck__() and __subclasscheck__()...
            and BaseConfiguration in type(value).mro()
            and not hasattr(value, _CONTEXT_ATTRIBUTE)
        ):
            context.enter(name).bind_to(value)
        return value

    def __getattribute__(self, attr):
        value = super().__getattribute__(attr)
        if isinstance(value, BaseConfiguration):
            return self._ensure_bound(attr, value)
        return value


class Configuration(BaseConfiguration, root=True):

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
        cls.update_forward_refs()
        if isinstance(spec, str):
            spec = ConfigSpec.from_str(spec)
        if create_missing is not None:
            spec.create_missing = create_missing
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        context: Context[ConfigT] = Context(spec)
        config = spec.read(config_class=cls, **kwargs)
        context.owner = config
        context.loaded = True
        context.original = config.dict()
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
        context = get_context(self)
        if not context.loaded:
            msg = "Configuration has not been loaded, use load() instead"
            raise ValueError(msg)
        if context.owner is self:
            context.loaded = False
            kwargs.setdefault("mode", "r")
            kwargs.setdefault("create_kwds", {"mode": "w"})
            new_config = context.spec.read(**kwargs)
            context.original = new_config.dict()
            new_config.rollback()
            context.loaded = True
            return new_config
        msg = "partial reloading is not supported yet"
        raise ValueError(msg)

    def save(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        context = get_context(self)
        if context.owner is self:
            blob = context.spec.engine.dump(self)
            result = self.write(blob, **kwargs)
            context.original = self.dict()
            return result
        return save(context.section)

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
        context = get_context(self)
        if context.spec.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return context.spec.write(blob, **kwargs)


class AsyncConfiguration(BaseConfiguration, root=True):

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
            spec = ConfigSpec.from_str(spec)
        kwargs.setdefault("mode", "r")
        if create_missing:
            kwargs.setdefault("create_kwds", {"mode": "w"})
        context: Context[AsyncConfigT] = Context(spec)
        config = spec.read(config_class=cls, **kwargs)
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
        context = get_context(self)
        if not context.loaded:
            msg = "Configuration has not been loaded, use load() instead"
            raise ValueError(msg)
        context.loaded = False
        kwargs.setdefault("mode", "r")
        kwargs.setdefault("create_kwds", {"mode": "w"})
        new_async_config = await context.spec.read_async(**kwargs)
        context.original = new_async_config.dict()
        context.loaded = True
        return new_async_config

    async def save_async(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        context = get_context(self)
        if context.owner is self:
            blob = context.spec.engine.dump(self)
            result = await self.write_async(blob, **kwargs)
            context.original = self.dict()
            return result
        return await save_async(context.section)

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
        context = get_context(self)
        if context.spec.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return await context.spec.write_async(blob, **kwargs)
