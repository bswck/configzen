"""Core configuration classes and functions."""

from __future__ import annotations

import abc
import contextlib
import copy
import dataclasses
import functools
import io
import os
import pathlib
import collections.abc
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, Literal, NamedTuple, TypeVar, cast, \
    overload

import anyconfig
import pydantic
from pydantic.main import ModelMetaclass

from configzen.errors import UnknownParserError, ConfigItemAccessError

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore[assignment]
    AIOFILES_AVAILABLE = False

__all__ = (
    "ConfigResource",
    "ConfigModelBase",
    "ConfigModel",
    "AsyncConfigModel",
    "Meta",
    "save",
    "save_async",
    "reload",
    "reload_async",
    "convert",
    "converter",
    "convert_namedtuple",
    "convert_mapping",
    "load",
    "loader",
)

_URL_SCHEMES: set[str] = set(
    urllib.parse.uses_relative 
    + urllib.parse.uses_netloc 
    + urllib.parse.uses_params
) - {""}
_CONTEXT: str = "__config_context__"

T = TypeVar("T")

ContextT = TypeVar("ContextT", bound="AnyContext")
ConfigModelBaseT = TypeVar("ConfigModelBaseT", bound="ConfigModelBase")
ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
AsyncConfigModelT = TypeVar("AsyncConfigModelT", bound="AsyncConfigModel")

OpenedT = contextlib.AbstractContextManager


def _get_defaults_from_model_class(
    model: type[pydantic.BaseSettings]
) -> dict[str, Any]:
    defaults = {}
    for field in model.__fields__.values():
        default = field.default
        if not field.field_info.exclude and not field.required:
            if isinstance(default, pydantic.BaseSettings):
                default = default.dict()
            defaults[field.name] = default
    return defaults


def _vars(obj: Any) -> dict[str, Any]:
    obj_dict = obj
    if not isinstance(obj, dict):
        obj_dict = vars(obj)
    return obj_dict


def _is_namedtuple(
    obj: Any,
) -> bool:
    return (
        isinstance(obj, tuple) and
        hasattr(obj, '_asdict') and
        hasattr(obj, '_fields')
    )


@functools.singledispatch
def convert(obj):
    """Convert a value to a format that can be safely serialized."""
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if _is_namedtuple(obj):
        return convert_namedtuple(obj)
    return obj


_convert_register = convert.register


@overload
def converter(func: Callable[[T], Any], cls: None = None) -> Callable[[type[T]], Any]:
    ...


@overload
def converter(func: Callable[[T], Any], cls: type[T] = ...) -> type[T]:
    ...


def converter(func, cls=None):
    """Register a converter function for a type."""
    if cls is None:
        return functools.partial(converter, func)

    _convert_register(cls, func)
    
    if not hasattr(cls, "__get_validators__"):
        def validator_gen():
            yield lambda value: load.dispatch(cls)(cls, value)
        
        cls.__get_validators__ = validator_gen
    
    return cls


@functools.singledispatch
def load(cls, value):
    if isinstance(value, cls):
        return value    
    return cls(value)


def loader(func, cls=None):
    """Register a loader function for a type."""
    if cls is None:
        return functools.partial(loader, func)
    
    load.register(cls, func)
    return cls


@convert.register
def convert_mapping(obj: collections.abc.Mapping) -> dict[str, Any]:
    return {k: convert(v) for k, v in obj.items()}


@functools.singledispatch
def convert_namedtuple(obj) -> dict[str, Any]:
    # Initially I wanted it to be convert(obj._asdict()), but
    # pydantic doesn't seem to be friends with custom NamedTuples.
    return convert(list(obj))


def split_ac_options(options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a dictionary of options into those for loading and those for dumping."""
    load_options = {}
    dump_options = {}
    for key, value in options.items():
        if key.startswith("dump_"):
            key = key.removeprefix("dump_")
            targets = [dump_options]
        elif key.startswith("load_"):
            key = key.removeprefix("load_")
            targets = [dump_options]
        else:
            targets = [load_options, dump_options]
        for target in targets:
            if key in target:
                raise ValueError(f"overlapping option {key}={value!r}")
            target[key] = value

    return load_options, dump_options


class ConfigResource:
    """A configuration resource to read from/write to."""

    _resource: OpenedT | str | os.PathLike | pathlib.Path
    defaults: dict[str, Any]
    create_if_missing: bool
    ac_parser: str | None
    _ac_load_options: dict[str, Any]
    _ac_dump_options: dict[str, Any]
    cache_engine: bool
    allowed_url_schemes: set[str] = _URL_SCHEMES

    def __init__(
        self: ConfigResource,
        resource: OpenedT[str] | str,
        ac_parser: str | None = None,
        *,
        create_if_missing: bool = False,
        use_pydantic_json: bool = True,
        **options: Any,
    ) -> None:
        """Parameters
        ----------
        resource : str or file-like object, optional
            The URL to the configuration file, or a file-like object.
        ac_parser : str, optional
            The name of the engines to use for loading and saving the configuration.
            Defaults to 'yaml'.
        create_if_missing : bool, optional
            Whether to automatically create missing keys when loading the configuration.
        use_pydantic_json : bool, optional
            Whether to use Pydantic's JSON encoder/decoder instead of the default
            anyconfig one.
        **options
            Additional keyword arguments to pass to 
            `anyconfig.loads()` and `anyconfig.dumps()`.
        """
        self.ac_parser = ac_parser
        self.resource = resource
        self.create_if_missing = create_if_missing
        self.use_pydantic_json = use_pydantic_json
        self._ac_load_options, self._ac_dump_options = split_ac_options(options)

    @property
    def resource(self) -> OpenedT[str] | str | os.PathLike | pathlib.Path:
        return self._resource
    
    @resource.setter
    def resource(self, value: OpenedT[str] | str | os.PathLike | pathlib.Path) -> None:
        self._resource = value
        self.ac_parser = self.ac_parser or self._guess_ac_parser()

    def _guess_ac_parser(self) -> str | None:
        ac_parser = None
        if isinstance(self.resource, str):
            ac_parser = pathlib.Path(self.resource).suffix[1:].casefold()
            if not ac_parser:
                raise UnknownParserError(
                    f"Could not guess the engine to use for {self.resource!r}."
                )
        return ac_parser

    def load_into(
        self, 
        config_class: type[ConfigModelBaseT], 
        blob: str,
        ac_parser: str | None = None, 
        **kwargs: Any
    ) -> ConfigModelBaseT:
        config = self.load_into_dict(blob, ac_parser=ac_parser, **kwargs)
        if config is None:
            config = {}
        return config_class(**config)

    def load_into_dict(
        self,
        blob: str,
        ac_parser=None,
        **kwargs: Any
    ) -> dict[str, Any]:
        if ac_parser is None:
            ac_parser = self.ac_parser
        kwargs = {**self._ac_load_options, **kwargs}        
        return anyconfig.loads(blob, ac_parser=ac_parser, **kwargs)

    def dump_config(
        self, 
        config: ConfigModelBaseT,
        ac_parser=None,
        **kwargs: Any
    ) -> str:
        if ac_parser is None:
            ac_parser = self.ac_parser
        if ac_parser == "json" and self.use_pydantic_json:
            return config.json(**kwargs)
        data = config.dict()
        return self.dump_data(data, ac_parser=ac_parser, **kwargs)

    def dump_data(
        self,
        data: dict[str, Any],
        ac_parser=None,
        **kwargs: Any
    ) -> str:
        if ac_parser is None:
            ac_parser = self.ac_parser
        kwargs = {**self._ac_dump_options, **kwargs}        
        return anyconfig.dumps(
            convert(data),
            ac_parser=ac_parser,
            **kwargs
        )

    @property
    def is_url(self) -> bool:
        """Whether the entrypoint is a URL."""
        return (
            isinstance(self.resource, str)
            and urllib.parse.urlparse(self.resource).scheme in _URL_SCHEMES
        )

    def open_resource(self, **kwds: Any) -> OpenedT:
        """Open the configuration file.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.
        """
        if self.resource is None:
            return io.StringIO()
        if self.is_url:
            url = cast(str, self.resource)
            if urllib.parse.urlparse(url).scheme not in self.allowed_url_schemes:
                msg = (
                    f"URL scheme {urllib.parse.urlparse(url).scheme!r} is not allowed, "
                    f"must be one of {self.allowed_url_schemes!r}"
                )
                raise ValueError(msg)
            return urllib.request.urlopen(urllib.request.Request(url), **kwds)  # noqa: S310, ^
        if isinstance(self.resource, (str, os.PathLike, pathlib.Path)):
            return pathlib.Path(self.resource).open(**kwds)
        return self.resource

    def open_resource_async(self, **kwds: Any) -> Any:
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
            raise RuntimeError(msg)
        return aiofiles.open(cast(str, self.resource), **kwds)

    def _get_default_kwargs(
        self, operation: Literal["read", "write"], 
        kwargs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if kwargs is None:
            kwargs = {}
        if not self.is_url:
            if operation == "read":
                kwargs.setdefault("mode", "r")
            elif operation == "write":
                kwargs.setdefault("mode", "w")
            else:
                raise ValueError(f"invalid method {operation!r}")
        return kwargs

    def read(
        self,
        *,
        config_class: type[ConfigModelBaseT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ConfigModelBaseT:
        """Read the configuration file.

        Parameters
        ----------
        config_class
        create_kwargs : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwargs
            Keyword arguments to pass to the open method.
        """
        kwargs = self._get_default_kwargs("read", kwargs=kwargs)
        try:
            with self.open_resource(**kwargs) as fp:
                blob = fp.read()
        except FileNotFoundError:
            blob = None
            if self.create_if_missing:
                defaults = _get_defaults_from_model_class(config_class)
                blob = self.dump_data(defaults)
                create_kwargs = self._get_default_kwargs("write", kwargs=create_kwargs)
                self.write(blob, **create_kwargs)
        return self.load_into(config_class, blob, **self._ac_load_options)

    def write(self, blob: str | collections.abc.ByteString, **kwds: Any) -> int:
        with self.open_resource(**kwds) as fp:
            return fp.write(blob)

    async def read_async(
        self,
        *,
        config_class: type[ConfigModelT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """Read the configuration file asynchronously.

        Parameters
        ----------
        config_class
        create_kwargs : dict, optional
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwargs
            Keyword arguments to pass to the open method.
        """
        kwargs = self._get_default_kwargs("read", kwargs=kwargs)
        try:
            async with self.open_resource_async(**kwargs) as fp:
                blob = await fp.read()
        except FileNotFoundError:
            if self.create_if_missing:
                defaults = _get_defaults_from_model_class(config_class)
                blob = self.dump_data(defaults)
                create_kwargs = self._get_default_kwargs("write", kwargs=create_kwargs)
                await self.write_async(blob, **create_kwargs)
        return self.load_into(config_class, blob, **self._ac_load_options)

    async def write_async(
        self, 
        blob: str | collections.abc.ByteString, **kwds: Any
    ) -> int:
        async with self.open_resource_async(**kwds) as fp:
            return await fp.write(blob)


if TYPE_CHECKING:

    class ConfigAt(NamedTuple, Generic[ConfigModelBaseT]):
        owner: ConfigModelBaseT
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
        owner: ConfigModelBaseT
        mapping: dict[str, Any] | None
        route: list[str]

        def get(self) -> Any:
            scope = _vars(self.mapping or self.owner.dict())
            route_here = []
            try:
                for part in self.route:
                    route_here.append(part)
                    scope = _vars(scope)[part]
            except KeyError:
                raise ConfigItemAccessError(self.owner, route_here) from None
            return scope

        def update(self, value: Any) -> collections.abc.MutableMapping:
            route = list(self.route)
            mapping = self.mapping or self.owner.dict()
            key = route.pop()
            submapping = _vars(mapping)
            route_here = []
            try:
                for part in route:
                    route_here.append(part)
                    submapping = _vars(submapping[part])
                else:
                    submapping[key] = value
            except KeyError:
                raise ConfigItemAccessError(self.owner, route_here) from None
            return mapping

        async def save_async(self) -> int:
            return await save_async(self)

        def save(self) -> int:
            return save(self)

        async def reload_async(self) -> Any:
            return await reload_async(self)

        def reload(self) -> Any:
            return reload(self)


def save(section: ConfigModelT | ConfigAt) -> int:
    if isinstance(section, ConfigModel):
        config = section
        return config.save()

    config = section.owner
    data = config.original
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = get_context(config)
    blob = context.resource.dump_config(config.copy(update=data))
    result = config.write(blob)
    context.original = data
    return result


async def save_async(section: AsyncConfigModelT | ConfigAt) -> int:
    if isinstance(section, AsyncConfigModel):
        config = section
        return await config.save_async()

    config = section.owner
    data = config.original
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = get_context(config)
    blob = context.resource.dump_config(config.copy(update=data))
    result = await config.write_async(blob)
    context.original = data
    return result


def reload(section: ConfigModelT | ConfigAt) -> Any:
    if isinstance(section, ConfigModel):
        config = section
        return config.reload()
    
    config = section.owner
    context = get_context(config)
    data = config.dict()
    newest = context.resource.read(config_class=type(config))
    section_data = ConfigAt(newest, newest.dict(), section.route).get()    
    new_mapping = ConfigAt(config, data, section.route).update(section_data)
    config.__dict__.update(new_mapping) 
    return section_data


async def reload_async(section: AsyncConfigModelT | ConfigAt) -> Any:
    if isinstance(section, AsyncConfigModel):
        config = section
        return await config.reload_async()
    
    config = section.owner
    context = get_context(config)
    data = config.dict()
    newest = await context.resource.read_async(config_class=type(config))
    section_data = ConfigAt(newest, newest.dict(), section.route).get()    
    new_mapping = ConfigAt(config, data, section.route).update(section_data)
    config.__dict__.update(new_mapping) 
    return new_mapping


class AnyContext(abc.ABC, Generic[ConfigModelBaseT]):
    original: dict[str, Any]
    _original: dict[str, Any]
    loaded: bool

    @abc.abstractmethod
    def trace_route(self) -> collections.abc.Generator[str, None, None]:
        """Trace the route to the configuration context."""

    @staticmethod
    def get(config: ConfigModelBaseT) -> AnyContext[ConfigModelBaseT]:
        return object.__getattribute__(config, _CONTEXT)

    def bind_to(self, config: ConfigModelBaseT) -> None:
        if config is None:
            return
        object.__setattr__(config, _CONTEXT, self)

    def enter(self, key: str) -> Subcontext[ConfigModelBaseT]:
        return Subcontext(self, key)

    @property
    @abc.abstractmethod
    def resource(self) -> ConfigResource:
        ...

    @property
    @abc.abstractmethod
    def owner(self) -> ConfigModelBaseT | None:
        ...

    @property
    @abc.abstractmethod
    def section(self) -> ConfigModelBaseT | ConfigAt[ConfigModelBaseT]:
        ...


class Context(AnyContext, Generic[ConfigModelBaseT]):
    def __init__(
        self, 
        resource: ConfigResource, 
        owner: ConfigModelBaseT | None = None
    ) -> None:
        self._resource = resource
        self._owner = None
        self._original = {}
        self._loaded = False

        self.owner = owner

    def trace_route(self) -> collections.abc.Generator[str, None, None]:
        yield from ()

    @property
    def resource(self) -> ConfigResource:
        return self._resource

    @property
    def section(self) -> ConfigModelBaseT | None:
        return self.owner

    @property
    def owner(self) -> ConfigModelBaseT | None:
        return self._owner

    @owner.setter
    def owner(self, config: ConfigModelBaseT | None) -> None:
        if config is None:
            return
        self.bind_to(config)
        self._owner = config

    @property
    def original(self) -> dict[str, Any]:
        return copy.deepcopy(self._original)

    @original.setter
    def original(self, original: dict[str, Any]) -> None:
        self._original = copy.deepcopy(original)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        self._loaded = value


class Subcontext(AnyContext, Generic[ConfigModelBaseT]):
    def __init__(self, parent: AnyContext[ConfigModelBaseT], key: str) -> None:
        self.parent = parent
        self.key = key

    @property
    def resource(self) -> ConfigResource:
        return self.parent.resource
        
    def trace_route(self) -> collections.abc.Generator[str, None, None]:
        yield from self.parent.trace_route()
        yield self.key

    @property
    def section(self) -> ConfigAt[ConfigModelBaseT]:
        if self.owner is None:
            msg = "Cannot get section for unbound context"
            raise ValueError(msg)
        return ConfigAt(self.owner, None, list(self.trace_route()))

    @property
    def owner(self) -> ConfigModelBaseT | None:
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


def get_context(config: ConfigModelBaseT) -> AnyContext[ConfigModelBaseT]:
    context = AnyContext.get(config)
    if context is None:
        msg = "Cannot get context for unbound configuration"
        raise RuntimeError(msg)
    return context


def json_encoder(model_encoder, value, **kwargs):
    original_type = type(value)
    converted_value = convert(value)
    if isinstance(converted_value, original_type):
        return model_encoder(value, **kwargs)
    return converted_value


class CMBMetaclass(ModelMetaclass):
    def __new__(mcs, name, bases, namespace, **kwargs):
        namespace[_CONTEXT] = pydantic.PrivateAttr()        
        if kwargs.pop("root", None):
            return type.__new__(mcs, name, bases, namespace, **kwargs)
        new_class = super().__new__(mcs, name, bases, namespace, **kwargs)        
        new_class.__json_encoder__ = functools.partial(
            json_encoder, 
            new_class.__json_encoder__
        )
        return new_class        


class ConfigModelBase(
    pydantic.BaseSettings, 
    metaclass=CMBMetaclass, 
    root=True
):
    """A configuration dictionary."""
    
    @classmethod
    def _resolve_resource(
        cls, 
        resource=None, 
        *, 
        create_if_missing: bool | None = None
    ):
        if resource is None:
            resource = getattr(cls.__config__, "resource", None)
        if isinstance(resource, str):
            resource = ConfigResource(resource)
        if create_if_missing is not None:
            resource.create_if_missing = create_if_missing
        return resource
    
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
        self: ConfigModelBaseT,
        route: str | list[str],
        *,
        parse_dotlist: bool = True,
    ) -> ConfigAt[ConfigModelBaseT]:
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
        self.__dict__ = context.original
        context.loaded = True

    def _ensure_settings_with_context(self, name, value):
        context = get_context(self)
        if (
            context 
            # pydantic.BaseModel.__instancecheck__() and __subclasscheck__()...
            and ConfigModelBase in type(value).mro()
            and not hasattr(value, _CONTEXT)
        ):
            context.enter(name).bind_to(value)
        return value

    def __getattribute__(self, attr):
        value = super().__getattribute__(attr)
        if isinstance(value, ConfigModelBase):
            return self._ensure_settings_with_context(attr, value)
        return value


class ConfigModel(ConfigModelBase, root=True):

    @classmethod
    def load(
        cls: type[ConfigModelT],
        resource: ConfigResource | str | None = None,
        create_if_missing: bool | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """Load the configuration file.
        To reload the configuration, use the ``reload`` method.

        Parameters
        ----------
        resource : ConfigResource
            The configuration resource to read from/write to.
        create_if_missing : bool
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        cls.update_forward_refs()
        resource = cls._resolve_resource(resource, create_if_missing=create_if_missing)
        context = Context(resource)
        config = resource.read(config_class=cls, **kwargs)
        context.owner = config
        context.loaded = True
        context.original = config.dict()
        return config

    def reload(self: ConfigModelT, **kwargs: Any) -> ConfigModelT:
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
            new_config = context.resource.read(**kwargs)
            context.bind_to(new_config)
            context.original = new_config.dict()
            new_config.rollback()
            context.loaded = True
            return new_config
        return reload(context.section)

    def save(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        context = get_context(self)
        if context.owner is self:
            blob = context.resource.dump_config(self)
            result = self.write(blob, **kwargs)
            context.original = self.dict()
            return result
        return save(context.section)

    def write(self, blob: str | collections.abc.ByteString, **kwargs: Any) -> int:
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
        if context.resource.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return context.resource.write(blob, **kwargs)


class AsyncConfigModel(ConfigModelBase, root=True):

    @classmethod
    async def load_async(
        cls: type[AsyncConfigModelT],
        resource: ConfigResource | str | None,
        *,
        create_if_missing: bool = False,
        **kwargs: Any,
    ) -> AsyncConfigModelT:
        """Load the configuration file asynchronously.
        To reload the configuration, use the ``reload`` method.

        Parameters
        ----------
        resource : ConfigResource
            The configuration resource.
        create_if_missing : bool
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        resource = cls._resolve_resource(resource, create_if_missing=create_if_missing)
        kwargs.setdefault("mode", "r")
        context = Context(resource)
        config = resource.read(config_class=cls, **kwargs)
        context.owner = config
        context.loaded = True
        return config

    async def reload_async(self: AsyncConfigModelT, **kwargs: Any) -> AsyncConfigModelT:
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
            new_async_config = await context.resource.read_async(**kwargs)
            context.bind_to(new_async_config)
            context.original = new_async_config.dict()
            self.rollback()
            context.loaded = True
            return new_async_config
        return await reload_async(context.section)

    async def save_async(self, **kwargs: Any) -> int:
        """Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        """
        context = get_context(self)
        if context.owner is self:
            blob = context.resource.dump_config(self)
            result = await self.write_async(blob, **kwargs)
            context.original = self.dict()
            return result
        return await save_async(context.section)

    async def write_async(self, blob: str | collections.abc.ByteString, **kwargs: Any) -> int:
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
        if context.resource.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        kwargs.setdefault("mode", "w")
        return await context.resource.write_async(blob, **kwargs)


class Meta(pydantic.BaseSettings.Config):
    resource: ConfigResource | str | None = None
