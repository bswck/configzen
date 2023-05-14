"""
The core module of the _configzen_ library.

This module provides a way to manage configuration files and resources
in a consistent way. It also provides a way to load and save configuration
files in a type-safe way.

.. code-block:: python

    from configzen import ConfigModel, ConfigResource, ConfigField, Meta

    class DatabaseConfig(ConfigModel):
        host: str
        port: int
        user: str
        password: str = ConfigField(exclude=True)

        class Config(Meta):
            resource = "examples/database.json"

    db_config = DatabaseConfig.load()
    db_config.host = "newhost"
    db_config.port = 5432

    db_config.save()

    db_config = DatabaseConfig.load()
    print(db_config.host)
    print(db_config.port)

    # Output:
    # newhost
    # 5432

    db_config.host = "otherhost"
    db_config.port = 5433

    db_config.at("host").save()

    print(db_config.host)
    print(db_config.port)

    # Output:
    # otherhost
    # 5432  # <- not 5433, because we saved only host

    db_config.host = "anotherhost"
    db_config.at("port").reload()

    print(db_config.host)
    print(db_config.port)

    # Output:
    # otherhost  # <- not anotherhost, because we reloaded only port
    # 5432
"""

from __future__ import annotations

import abc
import collections.abc
import contextlib
import copy
import dataclasses
import functools
import io
import os
import pathlib
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any, Generic, Literal, NamedTuple, TypeVar, cast

import anyconfig
import pydantic
from pydantic.main import ModelMetaclass

from configzen.errors import ConfigItemAccessError, UnknownParserError

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
    + urllib.parse.uses_params,
) - {""}
_CONTEXT: str = "__config_context__"

T = TypeVar("T")

ContextT = TypeVar("ContextT", bound="AnyContext")
ConfigModelBaseT = TypeVar("ConfigModelBaseT", bound="ConfigModelBase")
ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
AsyncConfigModelT = TypeVar("AsyncConfigModelT", bound="AsyncConfigModel")

OpenedT = contextlib.AbstractContextManager
RawResourceT = OpenedT | str | os.PathLike | pathlib.Path


def _get_defaults_from_model_class(
    model: type[pydantic.BaseSettings],
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
        hasattr(obj, "_asdict") and
        hasattr(obj, "_fields")
    )


@functools.singledispatch
def convert(obj: Any) -> Any:
    """
    Convert a value to a format that can be safely serialized.

    This function is used to convert values that are not supported by
    `anyconfig` to a format that can be safely serialized. It is used
    internally by `ConfigModel` and `AsyncConfigModel` to convert
    values before saving them to a file.

    Parameters
    ----------
    obj
        The value to convert.

    Returns
    -------
    Any
    """
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if _is_namedtuple(obj):
        return convert_namedtuple(obj)
    return obj


_convert_register = convert.register


def converter(func: Callable[[T], Any], cls: type[T] | None = None) -> type[T] | Any:
    """
    Register a converter function for a type.

    Parameters
    ----------
    func
        The converter function.

    cls
        The type to register the converter for.
        Optional for the decoration syntax.

    Returns
    -------
    The conversion result class.

    Usage
    -----
    .. code-block:: python

        @converter(converter_func)
        class MyClass:
            ...

    """
    if cls is None:
        return functools.partial(converter, func)

    _convert_register(cls, func)

    if not hasattr(cls, "__get_validators__"):
        def validator_gen() -> Generator[Callable[[Any], Any], None, None]:
            yield lambda value: load.dispatch(cls)(cls, value)

        cls.__get_validators__ = validator_gen  # type: ignore[attr-defined]

    return cls


@functools.singledispatch
def load(cls: Any, value: Any) -> Any:
    """
    Load a value into a type.

    This function is used to load values that are not supported by
    `anyconfig` to a format that can be used at runtime. It is used
    by pydantic while performing validation.

    Parameters
    ----------
    cls
        The type to load the value into.

    value
        The value to load.

    Returns
    -------
    The loaded value.
    """
    if isinstance(value, cls):
        return value
    return cls(value)


def loader(func: Callable[[Any], T], cls: type[T] | None = None) -> type[T] | Any:
    """
    Register a loader function for a type.

    Parameters
    ----------
    func
        The loader function.
    cls
        The type to register the loader for.

    Returns
    -------
    The loading result class.
    """

    if cls is None:
        return functools.partial(loader, func)

    load.register(cls, func)
    return cls


@convert.register
def convert_mapping(obj: collections.abc.Mapping) -> dict[str, Any]:
    """
    Convert a mapping to safely-serializable form.

    Parameters
    ----------
    obj
        The mapping to convert.

    Returns
    -------
    The converted mapping.
    """
    return {k: convert(v) for k, v in obj.items()}


@functools.singledispatch
def convert_namedtuple(obj: tuple) -> dict[str, Any]:
    """
    Convert a namedtuple to safely-serializable form.

    Parameters
    ----------
    obj
        The namedtuple to convert.

    Returns
    -------
    The converted namedtuple (likely a list).
    """
    # Initially I wanted it to be convert(obj._asdict()), but
    # pydantic doesn't seem to be friends with custom NamedTuples.
    return convert(list(obj))


def _split_ac_options(options: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    load_options: dict[str, Any] = {}
    dump_options: dict[str, Any] = {}
    for key, value in options.items():
        final_key = key
        if key.startswith("dump_"):
            final_key = key.removeprefix("dump_")
            targets = [dump_options]
        elif key.startswith("load_"):
            final_key = key.removeprefix("load_")
            targets = [dump_options]
        else:
            targets = [load_options, dump_options]
        for target in targets:
            if final_key in target:
                msg = (
                    f"option {key}={value!r} overlaps with "
                    f"defined {final_key}={target[final_key]!r}"
                )
                raise ValueError(msg)
            target[final_key] = value

    return load_options, dump_options


class ConfigResource:
    """
    A configuration resource.

    This class is used to represent a configuration resource, which
    can be a file, a URL, or a file-like object. It is used internally
    by `ConfigModel` and `AsyncConfigModel` to load and save
    configuration files.

    Parameters
    ----------
    resource
        The resource to load the configuration from.
    ac_parser
        The name of the engines to use for loading and saving the
        configuration. If not specified, the parser will be guessed
        from the file extension.
    create_if_missing
        Whether to create the file if it doesn't exist.
    use_pydantic_json
        Whether to use pydantic's JSON serialization for saving the
        configuration. This is useful for preserving the type of
        values that are not supported by `anyconfig`.
    options
        Additional options to pass to `anyconfig` API functions.

    Attributes
    ----------
    create_if_missing
        Whether to create the file if it doesn't exist.
    ac_parser
        The name of the engines to use for loading and saving the
        configuration. If not specified, the parser will be guessed
        from the file extension.
    allowed_url_schemes
        The URL schemes that are allowed to be used.

    Raises
    ------
    ValueError
    """

    _resource: OpenedT | str | os.PathLike | pathlib.Path
    create_if_missing: bool
    ac_parser: str | None
    _ac_load_options: dict[str, Any]
    _ac_dump_options: dict[str, Any]
    allowed_url_schemes: set[str] = _URL_SCHEMES

    def __init__(
        self: ConfigResource,
        resource: RawResourceT,
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
        if (
            not options.get("ac_safe")
            and options.get("load_ac_safe") is None
            and options.get("dump_ac_safe") is None
        ):
            # Business is business.
            options["ac_safe"] = True
        self.ac_parser = ac_parser
        self.resource = resource
        self.create_if_missing = create_if_missing
        self.use_pydantic_json = use_pydantic_json
        self._ac_load_options, self._ac_dump_options = _split_ac_options(options)

    @property
    def resource(self) -> RawResourceT:
        """
        The resource of the configuration.

        This can be a file path, a URL, or a file-like object.

        Returns
        -------
        The resource of the configuration.
        """
        return self._resource

    @resource.setter
    def resource(self, value: RawResourceT) -> None:
        """
        The resource of the configuration.

        This can be a file path, a URL, or a file-like object.

        .. note::
            If the resource is a file path, the parser will be guessed
            from the file extension.

        Returns
        -------
        The resource of the configuration.
        """
        self._resource = value
        self.ac_parser = self.ac_parser or self._guess_ac_parser()

    def _guess_ac_parser(self) -> str | None:
        ac_parser = None
        if isinstance(self.resource, str):
            ac_parser = pathlib.Path(self.resource).suffix[1:].casefold()
            if not ac_parser:
                msg = f"Could not guess the engine to use for {self.resource!r}."
                raise UnknownParserError(
                    msg,
                )
        return ac_parser

    def load_into(
        self,
        config_class: type[ConfigModelBaseT],
        blob: str,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> ConfigModelBaseT:
        """
        Load the configuration into a `ConfigModel` subclass.

        Parameters
        ----------
        config_class
            The `ConfigModel` subclass to load the configuration into.
        blob
            The configuration to load.
        ac_parser
            The name of the engines to use for loading the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration.
        """
        config = self.load_into_dict(blob, ac_parser=ac_parser, **kwargs)
        if config is None:
            config = {}
        return config_class(**config)

    def load_into_dict(
        self,
        blob: str,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """
        Load the configuration into a dictionary. The dictionary is
        usually used to initialize a `ConfigModel` subclass. If the
        configuration is empty, None might be returned instead of a dictionary.

        Parameters
        ----------
        blob
            The configuration to load.
        ac_parser
            The name of the anyconfig parser to use for loading the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration dictionary.
        """
        if ac_parser is None:
            ac_parser = self.ac_parser
        kwargs = {**self._ac_load_options, **kwargs}
        return anyconfig.loads(blob, ac_parser=ac_parser, **kwargs)

    def dump_config(
        self,
        config: ConfigModelBaseT,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Dump the configuration to a string.

        Parameters
        ----------
        config
            The configuration to dump.
        ac_parser
            The name of the anyconfig parser to use for saving the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.dumps()`.

        Returns
        -------
        The dumped configuration.
        """
        if ac_parser is None:
            ac_parser = self.ac_parser
        if ac_parser == "json" and self.use_pydantic_json:
            return config.json(**kwargs)
        data = config.dict()
        return self.dump_data(data, ac_parser=ac_parser, **kwargs)

    def dump_data(
        self,
        data: dict[str, Any],
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Dump data to a string.

        Parameters
        ----------
        data
            The data to dump.
        ac_parser
            The name of the anyconfig parser to use for saving the configuration.
        kwargs
            Additional keyword arguments to pass to `anyconfig.dumps()`.

        Returns
        -------
        The dumped configuration.
        """
        if ac_parser is None:
            ac_parser = self.ac_parser
        kwargs = {**self._ac_dump_options, **kwargs}
        return anyconfig.dumps(
            convert(data),
            ac_parser=ac_parser,
            **kwargs,
        )

    @property
    def is_url(self) -> bool:
        """Whether the resource is a URL."""
        return (
            isinstance(self.resource, str)
            and urllib.parse.urlparse(self.resource).scheme in _URL_SCHEMES
        )

    def open_resource(self, **kwds: Any) -> OpenedT:
        """
        Open the configuration file.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.

        Returns
        -------
        The opened resource.
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
            return urllib.request.urlopen(  # noqa: S310, ^
                urllib.request.Request(url), **kwds
            )
        if isinstance(self.resource, str | os.PathLike | pathlib.Path):
            return pathlib.Path(self.resource).open(**kwds)
        return self.resource

    def open_resource_async(self, **kwds: Any) -> Any:
        """
        Open the configuration file asynchronously.

        Parameters
        ----------
        **kwds
            Keyword arguments to pass to the opening routine.

        Returns
        -------
        The opened resource.
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
        kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kwargs is None:
            kwargs = {}
        if not self.is_url:
            if operation == "read":
                kwargs.setdefault("mode", "r")
            elif operation == "write":
                kwargs.setdefault("mode", "w")
            else:
                msg = f"invalid method {operation!r}"
                raise ValueError(msg)
        return kwargs

    def read(
        self,
        *,
        config_class: type[ConfigModelBaseT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ConfigModelBaseT:
        """
        Read the configuration file.

        Parameters
        ----------
        config_class
            The configuration model class to load the configuration into.
        create_kwargs
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwargs
            Keyword arguments to pass to the open method.

        Returns
        -------
        The loaded configuration.
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
        """
        Write the configuration file.

        Parameters
        ----------
        blob
            The string/bytes to write into the resource.
        kwds
            Keyword arguments to pass to the opening routine.

        Returns
        -------
        The number of bytes written.
        """
        kwds = self._get_default_kwargs("write", kwds)
        with self.open_resource(**kwds) as fp:
            return fp.write(blob)

    async def read_async(
        self,
        *,
        config_class: type[AsyncConfigModelT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncConfigModelT:
        """
        Read the configuration file asynchronously.

        Parameters
        ----------
        config_class
            The configuration model class to load the configuration into.
        create_kwargs
            Keyword arguments to pass to the open method
            when optionally creating the file.
        **kwargs
            Keyword arguments to pass to the open method.

        Returns
        -------
        The loaded configuration.
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
        blob: str | collections.abc.ByteString, **kwds: Any,
    ) -> int:
        """
        Write the configuration file asynchronously.

        Parameters
        ----------
        blob
            The string/bytes to write into the resource.
        kwds
            Keyword arguments to pass to the opening routine.

        Returns
        -------
        The number of bytes written.
        """
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
        """
        A configuration item at a specific location.

        Attributes
        ----------
        owner
            The configuration model instance.
        mapping
            The mapping to use.
        route
            The route to the item.
        """

        owner: ConfigModelBaseT
        mapping: dict[str, Any] | None
        route: list[str]

        def get(self) -> Any:
            """
            Get the value of the item.

            Returns
            -------
            The value of the item.
            """
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
            """
            Update the value of the item with regard to this item mapping.

            Parameters
            ----------
            value
                The new value.

            Returns
            -------
            The updated mapping.
            """
            route = list(self.route)
            mapping = self.mapping or self.owner.dict()
            key = route.pop()
            submapping = _vars(mapping)
            route_here = []
            try:
                for part in route:
                    route_here.append(part)
                    submapping = _vars(submapping[part])
                submapping[key] = value
            except KeyError:
                raise ConfigItemAccessError(self.owner, route_here) from None
            return mapping

        async def save_async(self, **kwargs: Any) -> int:
            """
            Save the configuration asynchronously.

            Parameters
            ----------
            **kwargs
                Keyword arguments to pass to the saving function.

            Returns
            -------
            The number of bytes written.
            """
            return await save_async(self, **kwargs)

        def save(self, **kwargs: Any) -> int:
            """
            Save the configuration.

            Parameters
            ----------
            **kwargs
                Keyword arguments to pass to the saving function.

            Returns
            -------
            The number of bytes written.
            """
            return save(self, **kwargs)

        async def reload_async(self, **kwargs: Any) -> Any:
            """
            Reload the configuration asynchronously.

            Parameters
            ----------
            kwargs
                Keyword arguments to pass to the reloading function.

            Returns
            -------
            The reloaded configuration or its belonging item.
            """
            return await reload_async(self, **kwargs)

        def reload(self, **kwargs: Any) -> Any:
            """
            Reload the configuration.

            Parameters
            ----------
            kwargs
                Keyword arguments to pass to the reloading function.

            Returns
            -------
            The reloaded configuration or its belonging item.
            """
            return reload(self, **kwargs)


def save(section: ConfigModelT | ConfigAt, **kwargs: Any) -> int:
    """
    Save the configuration.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    **kwargs
        Keyword arguments to pass to the saving function.

    Returns
    -------
    The number of bytes written.
    """
    if isinstance(section, ConfigModel):
        config = section
        return config.save()

    config = section.owner
    data = config.initial_state
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = get_context(config)
    blob = context.resource.dump_config(config.copy(update=data))
    result = config.write(blob, **kwargs)
    context.initial_state = data
    return result


async def save_async(section: AsyncConfigModelT | ConfigAt, **kwargs: Any) -> int:
    """
    Save the configuration asynchronously.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    **kwargs
        Keyword arguments to pass to the saving function.

    Returns
    -------
    The number of bytes written.
    """
    if isinstance(section, AsyncConfigModel):
        config = section
        return await config.save_async(**kwargs)

    config = section.owner
    data = config.initial_state
    at = ConfigAt(config, data, section.route)
    data = at.update(section.get())
    context = get_context(config)
    blob = context.resource.dump_config(config.copy(update=data))
    result = await config.write_async(blob, **kwargs)
    context.initial_state = data
    return result


def reload(section: ConfigModelT | ConfigAt, **kwargs: Any) -> Any:
    """
    Reload the configuration.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    **kwargs
        Keyword arguments to pass to the reloading function.

    Returns
    -------
    The reloaded configuration or its belonging item.
    """
    if isinstance(section, ConfigModel):
        config = section
        return config.reload()

    config = section.owner
    context = get_context(config)
    data = config.dict()
    newest = context.resource.read(config_class=type(config), **kwargs)
    section_data = ConfigAt(newest, newest.dict(), section.route).get()
    new_mapping = ConfigAt(config, data, section.route).update(section_data)
    config.__dict__.update(new_mapping)
    return section_data


async def reload_async(section: AsyncConfigModelT | ConfigAt, **kwargs: Any) -> Any:
    """
    Reload the configuration asynchronously.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    kwargs
        Keyword arguments to pass to the reloading function.

    Returns
    -------
    The reloaded configuration or its belonging item.
    """
    if isinstance(section, AsyncConfigModel):
        config = section
        return await config.reload_async()

    config = section.owner
    context = get_context(config)
    data = config.dict()
    newest = await context.resource.read_async(config_class=type(config), **kwargs)
    section_data = ConfigAt(newest, newest.dict(), section.route).get()
    new_mapping = ConfigAt(config, data, section.route).update(section_data)
    config.__dict__.update(new_mapping)
    return new_mapping


class AnyContext(abc.ABC, Generic[ConfigModelBaseT]):
    """
    The base class for configuration context.
    Contexts are used to
    - store configuration resource information,
    - link configuration items to the configuration models they belong to,
    - keep track of the route leading to particular configuration
      items that are also ConfigModel subclasses.

    Attributes
    ----------
    initial_state
        The initial configuration state.

    """
    initial_state: dict[str, Any]
    _initial_state: dict[str, Any]

    @abc.abstractmethod
    def trace_route(self) -> collections.abc.Generator[str, None, None]:
        """Trace the route to the configuration context."""

    @staticmethod
    def get(config: ConfigModelBaseT) -> AnyContext[ConfigModelBaseT]:
        """
        Get the context of the configuration model.

        Parameters
        ----------
        config
            The configuration model instance.

        Returns
        -------
        The context of the configuration model.
        """
        return object.__getattribute__(config, _CONTEXT)

    def bind_to(self, config: ConfigModelBaseT) -> None:
        """
        Bind the context to the configuration model.

        Parameters
        ----------
        config
            The configuration model instance.

        Returns
        -------
        None
        """
        if config is None:
            return
        object.__setattr__(config, _CONTEXT, self)

    def enter(self, part: str) -> Subcontext[ConfigModelBaseT]:
        """
        Enter a subcontext.

        Parameters
        ----------
        part
            The name of the item nested in the item this context points to.

        Returns
        -------
        The new subcontext.
        """
        return Subcontext(self, part)

    @property
    @abc.abstractmethod
    def resource(self) -> ConfigResource:
        """The configuration resource."""

    @property
    @abc.abstractmethod
    def owner(self) -> ConfigModelBaseT | None:
        """
        The top-level configuration model instance,
        holding all adjacent contexts.
        """

    @property
    @abc.abstractmethod
    def section(self) -> ConfigModelBaseT | ConfigAt[ConfigModelBaseT]:
        """
        The configuration model instance or the configuration item
        this context points to.
        """


class Context(AnyContext, Generic[ConfigModelBaseT]):
    """
    The context of a configuration model.

    Parameters
    ----------
    resource
        The configuration resource.
    owner
        The top-level configuration model instance,
        holding all belonging subcontexts.
    """
    def __init__(
        self,
        resource: ConfigResource,
        owner: ConfigModelBaseT | None = None,
    ) -> None:
        self._resource = resource
        self._owner = None
        self._initial_state = {}

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
    def initial_state(self) -> dict[str, Any]:
        return copy.deepcopy(self._initial_state)

    @initial_state.setter
    def initial_state(self, initial_state: dict[str, Any]) -> None:
        self._initial_state = copy.deepcopy(initial_state)


class Subcontext(AnyContext, Generic[ConfigModelBaseT]):
    """
    The subcontext of a configuration model.

    Parameters
    ----------
    parent
        The parent context.
    part
        The name of the item nested in the item the parent context points to.
    """

    def __init__(self, parent: AnyContext[ConfigModelBaseT], part: str) -> None:
        self._parent = parent
        self._part = part

    @property
    def resource(self) -> ConfigResource:
        return self._parent.resource

    def trace_route(self) -> collections.abc.Generator[str, None, None]:
        yield from self._parent.trace_route()
        yield self._part

    @property
    def section(self) -> ConfigAt[ConfigModelBaseT]:
        if self.owner is None:
            msg = "Cannot get section for unbound context"
            raise ValueError(msg)
        return ConfigAt(self.owner, None, list(self.trace_route()))

    @property
    def owner(self) -> ConfigModelBaseT | None:
        return self._parent.owner

    @property
    def initial_state(self) -> dict[str, Any]:
        return self._parent.initial_state

    @initial_state.setter
    def initial_state(self, value: dict[str, Any]) -> None:
        data = self._parent.initial_state
        data[self._part] = copy.deepcopy(value)
        self._parent.initial_state = data


def get_context(config: ConfigModelBaseT) -> AnyContext[ConfigModelBaseT]:
    """
    Get the context of the configuration model.

    Parameters
    ----------
    config
        The configuration model instance.

    Returns
    -------
    The context of the configuration model.
    """
    context = AnyContext.get(config)
    if context is None:
        msg = "Cannot get context for unbound configuration"
        raise RuntimeError(msg)
    return context


def _json_encoder(
    model_encoder: Callable,
    value: Any, **kwargs: Any
) -> Any:
    initial_state_type = type(value)
    converted_value = convert(value)
    if isinstance(converted_value, initial_state_type):
        return model_encoder(value, **kwargs)
    return converted_value


class CMBMetaclass(ModelMetaclass):
    def __new__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any
    ) -> type:
        namespace[_CONTEXT] = pydantic.PrivateAttr()
        if kwargs.pop("root", None):
            return type.__new__(cls, name, bases, namespace, **kwargs)
        new_class = super().__new__(cls, name, bases, namespace, **kwargs)
        new_class.__json_encoder__ = functools.partial(
            _json_encoder,
            new_class.__json_encoder__,
        )
        return new_class


class ConfigModelBase(
    pydantic.BaseSettings,
    metaclass=CMBMetaclass,
    root=True,
):
    """
    The base class for configuration models.

    It is not recommended to inherit from this class directly for basic usage.
    Instead, use either :class:`ConfigModel` or :class:`AsyncConfigModel`.
    """

    @classmethod
    def _resolve_resource(
        cls,
        resource_argument: ConfigResource | RawResourceT | None = None,
        *,
        create_if_missing: bool | None = None,
    ) -> ConfigResource:
        if resource_argument is None:
            resource_argument = getattr(cls.__config__, "resource", None)
        if resource_argument is None:
            raise ValueError("No resource specified")
        if not isinstance(resource_argument, ConfigResource):
            resource = ConfigResource(resource_argument)
        else:
            resource = resource_argument
        if create_if_missing is not None:
            resource.create_if_missing = create_if_missing
        return resource

    @property
    def initial_state(self) -> dict[str, Any]:
        """
        The initial configuration state.

        It is a copy of the configuration state
        at the last time of loading, reloading or saving.
        """
        return get_context(self).initial_state

    def at(
        self: ConfigModelBaseT,
        route: str | list[str],
        *,
        parse_dotlist: bool = True,
    ) -> ConfigAt[ConfigModelBaseT]:
        """
        Lazily point to a specific item in the configuration.

        Parameters
        ----------
        route
            The access route to the item in this configuration.

        parse_dotlist
            Whether to parse the route as a dotlist, by default True.


        Returns
        -------
        The configuration accessor.
        """
        if isinstance(route, str):
            if parse_dotlist:
                [*route] = route.split(".")
            else:
                route = [route]
        return ConfigAt(self, None, route)

    def rollback(self) -> None:
        """
        Rollback the configuration to its initial state.

        Returns
        -------
        None
        """
        context = get_context(self)
        self.__dict__ = context.initial_state

    def _ensure_settings_with_context(
        self,
        name: str,
        value: ConfigModelBaseT
    ) -> ConfigModelBaseT:
        context = get_context(self)
        if (
            context
            # pydantic.BaseModel.__instancecheck__() and __subclasscheck__()...
            and ConfigModelBase in type(value).mro()
            and not hasattr(value, _CONTEXT)
        ):
            context.enter(name).bind_to(value)
        return value

    def __getattribute__(self, attr: str) -> Any:
        value = super().__getattribute__(attr)
        if isinstance(value, ConfigModelBase):
            return self._ensure_settings_with_context(attr, value)
        return value


class ConfigModel(ConfigModelBase, root=True):

    @classmethod
    def load(
        cls: type[ConfigModelT],
        resource: ConfigResource | RawResourceT | None = None,
        create_if_missing: bool | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration file.
        To reload the configuration, use the `reload()` method.

        Parameters
        ----------
        resource
            The configuration resource to read from/write to.
        create_if_missing
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        cls.update_forward_refs()
        resource = cls._resolve_resource(resource, create_if_missing=create_if_missing)
        context = Context(resource)  # type: Context[ConfigModelT]
        config = resource.read(config_class=cls, **kwargs)
        context.owner = config
        context.initial_state = config.dict()
        return config

    def reload(self: ConfigModelT, **kwargs: Any) -> ConfigModelT:
        """
        Reload the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        context = get_context(self)
        if context.owner is self:
            new_config = context.resource.read(**kwargs)
            context.bind_to(new_config)
            context.initial_state = new_config.dict()
            new_config.rollback()
            return new_config
        return reload(context.section)

    def save(self, **kwargs: Any) -> int:
        """
        Save the configuration to the configuration file.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.owner is self:
            blob = context.resource.dump_config(self)
            result = self.write(blob, **kwargs)
            context.initial_state = self.dict()
            return result
        return save(context.section)

    def write(self, blob: str | collections.abc.ByteString, **kwargs: Any) -> int:
        """
        Overwrite the configuration file with the given string or bytes.

        Parameters
        ----------
        blob
            The blob to write to the configuration file.
        **kwargs
            Keyword arguments to pass to the open method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.resource.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        return context.resource.write(blob, **kwargs)


class AsyncConfigModel(ConfigModelBase, root=True):

    @classmethod
    async def load_async(
        cls: type[AsyncConfigModelT],
        resource: ConfigResource | RawResourceT | None,
        *,
        create_if_missing: bool = False,
        **kwargs: Any,
    ) -> AsyncConfigModelT:
        """
        Load the configuration file asynchronously.
        To reload the configuration, use the `reload()` method.

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
        context = Context(resource)  # type: Context[AsyncConfigModelT]
        config = resource.read(config_class=cls, **kwargs)
        context.owner = config
        return config

    async def reload_async(self: AsyncConfigModelT, **kwargs: Any) -> AsyncConfigModelT:
        """
        Reload the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        context = get_context(self)
        if context.owner is self:
            new_async_config = await context.resource.read_async(**kwargs)
            context.bind_to(new_async_config)
            context.initial_state = new_async_config.dict()
            self.rollback()
            return new_async_config
        return await reload_async(context.section)

    async def save_async(self, **kwargs: Any) -> int:
        """
        Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the write method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.owner is self:
            blob = context.resource.dump_config(self)
            result = await self.write_async(blob, **kwargs)
            context.initial_state = self.dict()
            return result
        return await save_async(context.section)

    async def write_async(
        self,
        blob: str | collections.abc.ByteString,
        **kwargs: Any
    ) -> int:
        """
        Overwrite the configuration file asynchronously with the given string or bytes.

        Parameters
        ----------
        blob
            The blob to write to the configuration file.
        **kwargs
            Keyword arguments to pass to the open method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.resource.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        return await context.resource.write_async(blob, **kwargs)


class Meta(pydantic.BaseSettings.Config):
    """
    Meta-configuration for the `ConfigModel` class.

    Attributes
    ----------
    resource
        The configuration resource to read from/write to.

        If a string, it will be interpreted as a path to a file.

    And all other attributes from `pydantic.BaseSettings.Config`.
    """
    resource: ConfigResource | RawResourceT | None = None
