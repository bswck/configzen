"""
The core module of the _configzen_ library.

This module provides an API to manage configuration files and resources
in a consistent way. It also provides tools to load and save configuration
files in various formats and within a number of advanced methods.

.. code-block:: python

    from configzen import ConfigModel, ConfigResource, ConfigField, ConfigMeta

    class DatabaseConfig(ConfigModel):
        host: str
        port: int
        user: str
        password: str = ConfigField(exclude=True)

        class Config(ConfigMeta):
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
import contextvars
import copy
import dataclasses
import functools
import io
import os
import pathlib
import urllib.parse
import urllib.request
from typing import (
    Any,
    ClassVar,
    Generic,
    Literal,
    Optional,
    cast,
    no_type_check,
    overload,
)

import anyconfig
import pydantic
from anyconfig.utils import filter_options, is_dict_like, is_list_like
from pydantic.fields import (  # type: ignore[attr-defined]
    ModelField,
    make_generic_validator,
)
from pydantic.json import ENCODERS_BY_TYPE
from pydantic.main import BaseModel, ModelMetaclass
from pydantic.utils import ROOT_KEY

from configzen.errors import (
    ConfigAccessError,
    InternalConfigError,
    ResourceLookupError,
    UnspecifiedParserError,
    pretty_syntax_error,
)
from configzen.processor import EXPORT, DirectiveContext, Processor
from configzen.typedefs import (
    AsyncConfigIO,
    ConfigIO,
    ConfigModelT,
    IncludeExcludeT,
    NormalizedResourceT,
    RawResourceT,
    SupportsRoute,
    T,
)

try:
    import aiofiles

    AIOFILES_AVAILABLE = True
except ImportError:
    aiofiles = None  # type: ignore[assignment]
    AIOFILES_AVAILABLE = False

__all__ = (
    "ConfigManager",
    "ConfigModel",
    "ConfigMeta",
    "save",
    "save_async",
    "reload",
    "reload_async",
    "pre_serialize",
    "with_pre_serialize",
    "post_deserialize",
    "with_post_deserialize",
    "export",
    "export_async",
    "with_exporter",
    "with_async_exporter",
)

_URL_SCHEMES: set[str] = set(
    urllib.parse.uses_relative + urllib.parse.uses_netloc + urllib.parse.uses_params
) - {""}
CONTEXT: str = "__context__"
TOKEN: str = "__context_token__"
LOCAL: str = "__local__"

current_context: contextvars.ContextVar[
    BaseContext[Any] | None
] = contextvars.ContextVar("current_context", default=None)

_exporting: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_exporting", default=False
)


def _get_defaults_from_model_class(
    model: type[pydantic.BaseModel],
) -> dict[str, Any]:
    defaults = {}
    for field in model.__fields__.values():
        default = field.default
        if not field.field_info.exclude and not field.required:
            if isinstance(default, pydantic.BaseModel):
                default = default.dict()
            defaults[field.name] = default
    return defaults


def _vars(obj: Any) -> dict[str, Any]:
    obj_dict = obj
    if not isinstance(obj, dict):
        obj_dict = obj.__dict__
    return cast(dict[str, Any], obj_dict)


def _is_namedtuple(
    obj: Any,
) -> bool:
    return (
        isinstance(obj, tuple) and hasattr(obj, "_asdict") and hasattr(obj, "_fields")
    )


@functools.singledispatch
def pre_serialize(obj: Any) -> Any:
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
        return pre_serialize(dataclasses.asdict(obj))
    if _is_namedtuple(obj):
        return _ps_namedtuple(obj)
    return obj


for obj_type, obj_encoder in ENCODERS_BY_TYPE.items():
    pre_serialize.register(obj_type, obj_encoder)


def with_pre_serialize(
    func: collections.abc.Callable[[T], Any], cls: type[T] | None = None
) -> type[T] | Any:
    """
    Register a pre-serialization converter function for a type.

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

        @with_pre_serialize(converter_func)
        class MyClass:
            ...

    """
    if cls is None:
        return functools.partial(with_pre_serialize, func)

    pre_serialize.register(cls, func)

    if not hasattr(cls, "__get_validators__"):

        def validator_gen() -> (
            collections.abc.Iterator[collections.abc.Callable[[Any], Any]]
        ):
            yield lambda value: post_deserialize.dispatch(cls)(cls, value)

        cls.__get_validators__ = validator_gen  # type: ignore[attr-defined]

    return cls


@functools.singledispatch
def post_deserialize(cls: Any, value: Any) -> Any:
    """
    Load a value into a type after deserialization.

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


def with_post_deserialize(
    func: collections.abc.Callable[[Any], T], cls: type[T] | None = None
) -> type[T] | Any:
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
        return functools.partial(with_post_deserialize, func)

    post_deserialize.register(cls, func)
    return cls


@functools.singledispatch
def export(obj: Any, **kwargs: Any) -> dict[str, Any]:
    """
    Export a ConfigModel to a safely-serializable format.
    Register a custom exporter for a type using the `with_exporter` decorator,
    which can help to exclude particular values from the export if needed.

    Parameters
    ----------
    obj
    """
    if isinstance(obj, ConfigModel) and not _exporting.get():
        return obj.export(**kwargs)
    return cast(dict[str, Any], obj.dict(**kwargs))


@functools.singledispatch
async def export_async(obj: Any, **kwargs: Any) -> dict[str, Any]:
    """
    Export a ConfigModel to a safely-serializable format.
    Register a custom exporter for a type using the `with_exporter` decorator,
    which can help to exclude particular values from the export if needed.

    Parameters
    ----------
    obj
    """
    if isinstance(obj, ConfigModel) and not _exporting.get():
        return await obj.export_async(**kwargs)
    return cast(dict[str, Any], await obj.dict_async(**kwargs))


def with_exporter(
    func: collections.abc.Callable[[ConfigModelT], dict[str, Any]],
    cls: type[ConfigModelT] | None = None,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a type.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    export.register(cls, func)
    if export_async.dispatch(cls) is export_async:

        async def default_async_func(obj: Any) -> Any:
            return func(obj)

        export_async.register(cls, default_async_func)
    return cls


def with_async_exporter(
    func: collections.abc.Callable[
        [ConfigModelT], collections.abc.Coroutine[Any, Any, dict[str, Any]]
    ],
    cls: type[ConfigModelT] | None = None,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a type.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    export_async.register(cls, func)
    return cls


@pre_serialize.register(list)
def _ps_list(obj: list[Any]) -> list[Any]:
    """
    Convert a list to safely-serializable form.

    Parameters
    ----------
    obj
        The list to convert.

    Returns
    -------
    The converted list.
    """
    return [pre_serialize(item) for item in obj]


@pre_serialize.register(collections.abc.Mapping)
def _ps_mapping(obj: collections.abc.Mapping[Any, Any]) -> dict[Any, Any]:
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
    return {k: pre_serialize(v) for k, v in obj.items()}


@functools.singledispatch
def _ps_namedtuple(obj: tuple[Any, ...]) -> Any:
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
    # Initially I wanted it to be pre_serialize(obj._asdict()), but
    # pydantic doesn't seem to be friends with custom NamedTuple-s.
    return pre_serialize(list(obj))


def _delegate_ac_options(
    load_options: dict[str, Any], dump_options: dict[str, Any], options: dict[str, Any]
) -> None:
    for key, value in options.items():
        if key.startswith("dump_"):
            actual_key = key.removeprefix("dump_")
            targets = [dump_options]
        elif key.startswith("load_"):
            actual_key = key.removeprefix("load_")
            targets = [load_options]
        else:
            actual_key = key
            targets = [load_options, dump_options]
        for target in targets:
            if actual_key in target:
                msg = (
                    f"option {key}={value!r} overlaps with "
                    f"defined {actual_key}={target[actual_key]!r}"
                )
                raise ValueError(msg)
            target[actual_key] = value


class ConfigManager(Generic[ConfigModelT]):
    """
    A configuration resource loader and saver.

    This class is used to represent a configuration resource, which
    can be a file, a URL, or a file-like object. It is used internally
    by `ConfigModel` and `AsyncConfigModel` to load and save
    configuration files.

    Parameters
    ----------
    resource
        The resource to load the configuration from.
    processor
        The resource processor to use. If not specified, the processor used will
        be :class:`DefaultProcessor`.
    ac_parser
        The name of the engines to use for loading and saving the
        configuration. If not specified, the processor will be guessed
        from the file extension.
    create_if_missing
        Whether to create the file if it doesn't exist.
    use_pydantic_json
        Whether to use pydantic's JSON serialization for saving the
        configuration. This is useful for preserving the type of
        values that are not supported by `anyconfig`.
    kwargs
        Additional options to pass to `anyconfig` API functions.

    Attributes
    ----------
    create_if_missing
        Whether to create the file if it doesn't exist.
    ac_parser
        The name of the engines to use for loading and saving the
        configuration. If not specified, the processor will be guessed
        from the file extension.
    allowed_url_schemes
        The URL schemes that are allowed to be used.

    Raises
    ------
    ValueError
    """

    _resource: NormalizedResourceT
    processor_class: type[Processor[ConfigModelT]]
    ac_parser: str | None
    create_if_missing: bool
    relative: bool = False
    allowed_url_schemes: set[str]
    use_pydantic_json: bool = True
    default_load_options: dict[str, Any] = {}
    default_dump_options: dict[str, Any] = {
        # These are usually desirable for configuration files.
        # If you want to change them, you can do so by monkey-patching
        # these variables. You can also change `load_options` and
        # `dump_options` instance attributes to make a local change.
        "allow_unicode": True,
        "ensure_ascii": False,
        "indent": 2,
    }

    predefined_default_kwargs: ClassVar[dict[str, Any]] = {"encoding": "UTF-8"}
    default_allowed_url_schemes: ClassVar[set[str]] = {"file", "http", "https"}

    OPEN_KWARGS: ClassVar[set[str]] = {
        "mode",
        "buffering",
        "encoding",
        "errors",
        "newline",
    }
    URLOPEN_KWARGS: ClassVar[set[str]] = {
        "data",
        "timeout",
        "cafile",
        "capath",
        "cadefault",
        "context",
    }
    JSON_KWARGS: ClassVar[set[str]] = {
        "skipkeys",
        "ensure_ascii",
        "check_circular",
        "allow_nan",
        "cls",
        "indent",
        "separators",
        "default",
        "sort_keys",
    }

    def __init__(
        self,
        resource: RawResourceT,
        ac_parser: str | None = None,
        processor_class: type[Processor[ConfigModelT]] | None = None,
        *,
        create_if_missing: bool = False,
        **kwargs: Any,
    ) -> None:
        """Parameters
        ----------
        resource
            The URL to the configuration file, or a file-like object.
        ac_parser
            The name of the engines to use for loading and saving the configuration.
            Defaults to 'yaml'.
        create_if_missing
            Whether to automatically create missing keys when loading the configuration.
        default_kwargs
            Default keyword arguments to pass while opening the resource.
        use_pydantic_json
            Whether to use Pydantic's JSON encoder/decoder instead of the default
            anyconfig one.
        **kwargs
            Additional keyword arguments to pass to
            `anyconfig.loads()` and `anyconfig.dumps()`.
        """
        if processor_class is None:
            processor_class = Processor[ConfigModelT]

        self.processor_class = processor_class
        self.ac_parser = ac_parser

        if isinstance(resource, (str, os.PathLike)) and not (
            isinstance(resource, str)
            and urllib.parse.urlparse(str(resource)).scheme in _URL_SCHEMES
        ):
            raw_path = os.fspath(resource)
            resource = pathlib.Path(raw_path)
            if raw_path.startswith(".") and len(resource.parts) > 1:
                self.relative = True

        self.resource = resource
        self.create_if_missing = create_if_missing
        self.use_pydantic_json = kwargs.pop("use_pydantic_json", True)
        self.default_kwargs = kwargs.pop(
            "default_kwargs", self.predefined_default_kwargs.copy()
        )
        self.allowed_url_schemes = kwargs.pop(
            "allowed_url_schemes", self.default_allowed_url_schemes.copy()
        )

        self.load_options = self.default_load_options.copy()
        self.dump_options = self.default_dump_options.copy()

        _delegate_ac_options(self.load_options, self.dump_options, kwargs)

    @property
    def resource(self) -> NormalizedResourceT:
        """
        The resource of the configuration.

        This can be a file path, a URL, or a file-like object.

        Returns
        -------
        The resource of the configuration.
        """
        return self._resource

    @resource.setter
    def resource(self, value: NormalizedResourceT) -> None:
        """
        The resource of the configuration.

        This can be a file path, a URL, or a file-like object.

        .. note::
            If the resource is a file path, the processor will be guessed
            from the file extension.

        Returns
        -------
        The resource of the configuration.
        """
        self._resource = value
        self.ac_parser = self.ac_parser or self._guess_ac_parser()

    def _guess_ac_parser(self) -> str | None:
        ac_parser = None
        if isinstance(self.resource, pathlib.Path):
            ac_parser = self.resource.suffix[1:].casefold()
            if not ac_parser:
                msg = f"Could not guess the engine to use for {self.resource!r}."
                raise UnspecifiedParserError(msg)
        return ac_parser

    def load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
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
        dict_config = self.load_into_dict(blob, ac_parser=ac_parser, **kwargs)
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    async def async_load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration into a `ConfigModel` subclass asynchronously.

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
        dict_config = await self.load_into_dict_async(
            blob,
            ac_parser=ac_parser,
            **kwargs
        )
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    def _load_into_dict_impl(
        self,
        blob: str,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if ac_parser is None:
            ac_parser = self.ac_parser
        kwargs = self.load_options | kwargs
        loaded = anyconfig.loads(  # type: ignore[no-untyped-call]
            blob, ac_parser=ac_parser, **kwargs
        )
        if not isinstance(loaded, collections.abc.Mapping):
            msg = (
                f"Expected a mapping as a result of loading {self.resource}, "
                f"got {type(loaded).__name__}."
            )
            raise TypeError(msg)
        return dict(loaded)

    def load_into_dict(
        self,
        blob: str,
        ac_parser: str | None = None,
        *,
        preprocess: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
        preprocess
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration dictionary.
        """
        loaded = self._load_into_dict_impl(blob, ac_parser, **kwargs)
        if preprocess:
            loaded = self.processor_class(self, loaded).preprocess()
        return loaded

    async def load_into_dict_async(
        self,
        blob: str,
        ac_parser: str | None = None,
        *,
        preprocess: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Load the configuration into a dictionary asynchronously.

        Parameters
        ----------
        blob
            The configuration to load.
        ac_parser
            The name of the anyconfig parser to use for loading the configuration.
        preprocess
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration dictionary.
        """
        loaded = self._load_into_dict_impl(blob, ac_parser, **kwargs)
        if preprocess:
            loaded = await self.processor_class(self, loaded).preprocess_async()
        return loaded

    def dump_config(
        self,
        config: ConfigModelT,
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
            kwargs = filter_options(self.JSON_KWARGS, self.dump_options | kwargs)
            return config.json(**kwargs)
        return self.dump_data(export(config), ac_parser=ac_parser, **kwargs)

    async def dump_config_async(
        self,
        config: ConfigModelT,
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
            kwargs = filter_options(self.JSON_KWARGS, self.dump_options | kwargs)
            return await config.json_async(**kwargs)
        return self.dump_data(await export_async(config), ac_parser=ac_parser, **kwargs)

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
        kwargs = self.dump_options | kwargs
        return anyconfig.dumps(pre_serialize(data), ac_parser=ac_parser, **kwargs)

    @property
    def is_url(self) -> bool:
        """Whether the resource is a URL."""
        return isinstance(self.resource, str)

    def open_resource(self, **kwds: Any) -> ConfigIO:
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
            url = urllib.parse.urlparse(cast(str, self.resource))
            if url.scheme not in self.allowed_url_schemes:
                msg = (
                    f"URL scheme {url.scheme!r} is not allowed, "
                    f"must be one of {self.allowed_url_schemes!r}"
                )
                raise ValueError(msg)
            kwds = filter_options(self.URLOPEN_KWARGS, kwds)
            request = urllib.request.Request(url.geturl())
            return cast(ConfigIO, urllib.request.urlopen(request, **kwds))  # noqa: S310
        if isinstance(self.resource, (int, pathlib.Path)):
            kwds = filter_options(self.OPEN_KWARGS, kwds)
            if isinstance(self.resource, int):
                return cast(
                    ConfigIO,
                    # We intentionally do not use the context manager here
                    # because we do not want to close the file.
                    # Moreover, we want to allow the file to be opened
                    # from a file descriptor, not supported by Path().
                    open(self.resource, **kwds),  # noqa: PTH123, SIM115
                )
            return cast(ConfigIO, pathlib.Path(self.resource).open(**kwds))
        return cast(ConfigIO, self.resource)

    def open_resource_async(self, **kwds: Any) -> AsyncConfigIO:
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
        if isinstance(self.resource, (int, pathlib.Path)):
            kwds = filter_options(self.OPEN_KWARGS, kwds)
            return aiofiles.open(self.resource, **kwds)
        raise RuntimeError("cannot open resource asynchronously")

    def processor_open_resource(self, **kwargs: Any) -> ConfigIO:
        """
        Called by the processor to open a configuration resource
        with the reading intention.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.

        Returns
        -------
        The opened resource.
        """
        kwargs = self._get_default_kwargs("read", kwargs)
        return self.open_resource(**kwargs)

    def processor_open_resource_async(self, **kwargs: Any) -> AsyncConfigIO:
        """
        Called by the processor to open a configuration resource asynchronously
        with the reading intention.

        Parameters
        ----------
        **kwargs
            Keyword arguments to pass to the opening routine.
            For URLs, these are passed to ``urllib.request.urlopen()``.
            For local files, these are passed to ``builtins.open()``.

        Returns
        -------
        The opened resource.
        """
        kwargs = self._get_default_kwargs("read", kwargs)
        return self.open_resource_async(**kwargs)

    def _get_default_kwargs(
        self,
        method: Literal["read", "write"],
        kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not kwargs:
            kwargs = self.default_kwargs
        new_kwargs = cast(dict[str, Any], kwargs).copy()
        if not self.is_url:
            if method == "read":
                new_kwargs.setdefault("mode", "r")
            elif method == "write":
                new_kwargs.setdefault("mode", "w")
            else:
                msg = f"invalid resource access method: {method!r}"
                raise ValueError(msg)
        return new_kwargs

    def read(
        self,
        *,
        config_class: type[ConfigModelT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
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
                self.write(blob, **(create_kwargs or {}))
            else:
                raise
        return self.load_into(config_class, cast(str, blob), **self.load_options)

    def write(self, blob: str, **kwargs: Any) -> int:
        """
        Write the configuration file.

        Parameters
        ----------
        blob
            The string/bytes to write into the resource.
        kwargs
            Keyword arguments to pass to the opening routine.

        Returns
        -------
        The number of bytes written.
        """
        kwargs = self._get_default_kwargs("write", kwargs=kwargs)
        with self.open_resource(**kwargs) as fp:
            return fp.write(blob)

    async def read_async(
        self,
        *,
        config_class: type[ConfigModelT],
        create_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
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
                await self.write_async(blob, **(create_kwargs or {}))
        return await self.async_load_into(config_class, blob, **self.load_options)

    async def write_async(
        self,
        blob: str,
        **kwargs: Any,
    ) -> int:
        """
        Write the configuration file asynchronously.

        Parameters
        ----------
        blob
            The string/bytes to write into the resource.
        kwargs
            Keyword arguments to pass to the opening routine.

        Returns
        -------
        The number of bytes written.
        """
        kwargs = self._get_default_kwargs("write", kwargs=kwargs)
        async with self.open_resource_async(**kwargs) as fp:
            return await fp.write(blob)

    @classmethod
    def from_directive_context(
        cls,
        ctx: DirectiveContext,
        /,
        route_separator: str = ":",
        route_class: type[Route] | None = None,
    ) -> tuple[ConfigManager[ConfigModelT], SupportsRoute | None]:
        """
        Create a configuration manager from a preprocessor directive context.
        Return an optional scope that the context points to.

        Parameters
        ----------
        route_class
        route_separator
        ctx

        Returns
        -------
        The configuration manager.
        """
        if route_class is None:
            route_class = Route
        route: SupportsRoute | None = None
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        if isinstance(ctx.snippet, str):
            path, _, route = ctx.snippet.partition(route_separator)
            route = Route(route.strip().replace(route_separator, route_class.TOK_DOT))
            args.append(path)
        elif isinstance(ctx.snippet, int):
            args.append(ctx.snippet)
        elif is_dict_like(ctx.snippet):
            kwargs |= ctx.snippet
        elif is_list_like(ctx.snippet):
            args += list(ctx.snippet)
        else:
            msg = (
                f"invalid snippet for the {ctx.directive!r} directive: {ctx.snippet!r}"
            )
            raise ValueError(msg)
        return cls(*args, **kwargs), str(route)

    def __repr__(self) -> str:
        resource = self.resource
        return f"{type(self).__name__}({resource=!r})"


class Route:
    TOK_DOT: ClassVar[str] = "."
    TOK_DOTLIST_ESCAPE_ENTER: ClassVar[str] = "["
    TOK_DOTLIST_ESCAPE_EXIT: ClassVar[str] = "]"

    def __init__(self, route: SupportsRoute) -> None:
        self.list_route = self.parse(route)

    @classmethod
    def parse(cls, route: SupportsRoute) -> list[str]:
        if isinstance(route, Route):
            return route.list_route
        if isinstance(route, list):
            return route
        if isinstance(route, str):
            with pretty_syntax_error(route):
                return cls._decompose(route)
        raise TypeError(f"invalid route type {type(route)!r}")

    @classmethod
    def _decompose(cls, route: str) -> list[str]:
        route = route.removesuffix(cls.TOK_DOT) + cls.TOK_DOT
        argument = ""
        escape_ctx = False
        prev_char: str | None = None
        list_route = []
        for char_no, char in enumerate(route, start=1):
            if char in cls.TOK_DOT:
                if escape_ctx:
                    argument += char
                else:
                    list_route.append(argument)
                    argument = ""
            elif char in cls.TOK_DOTLIST_ESCAPE_ENTER:
                if prev_char and prev_char in cls.TOK_DOTLIST_ESCAPE_EXIT:
                    raise InternalConfigError(
                        "invalid escape sequence "
                        f"(expected {cls.TOK_DOT} between escape sequences)",
                        extra=char_no,
                    )
                if escape_ctx:
                    msg = "invalid escape sequence"
                    raise InternalConfigError(msg, extra=char_no)
                escape_ctx = True
            elif char in cls.TOK_DOTLIST_ESCAPE_EXIT:
                if not escape_ctx:
                    msg = "invalid escape sequence"
                    raise InternalConfigError(msg, extra=char_no)
                escape_ctx = False
                list_route.append(argument)
                argument = ""
            else:
                argument += char
            prev_char = char
        return list_route

    @classmethod
    def decompose(cls, route: str) -> list[str]:
        with pretty_syntax_error(route):
            return cls._decompose(route)

    def compose(self) -> str:
        escape = (self.TOK_DOTLIST_ESCAPE_ENTER, self.TOK_DOTLIST_ESCAPE_EXIT)
        raw = ("", "")
        return self.TOK_DOT.join(
            fragment.join(escape) if self.TOK_DOT in fragment else fragment.join(raw)
            for fragment in self.list_route
        )

    def enter(self, *args: str) -> Route:
        return type(self)(self.list_route + list(args))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Route):
            return self.list_route == other.list_route
        if isinstance(other, str):
            return self.list_route == self.decompose(other)
        if isinstance(other, list):
            return self.list_route == other
        return NotImplemented

    def __str__(self) -> str:
        return self.compose()

    def __iter__(self) -> collections.abc.Iterator[str]:
        yield from self.list_route


def at(
    mapping: Any,
    route: SupportsRoute,
    converter_func: collections.abc.Callable[[Any], dict[str, Any]] = _vars,
    manager: ConfigManager[ConfigModelT] | None = None,
) -> Any:
    """
    Get an item at a route.

    Parameters
    ----------
    mapping
        The mapping to use.
    route
        The route to the item.
    converter_func
    manager

    Returns
    -------
    The item at the route.
    """
    route = Route(route)
    route_here = []
    scope = _vars(mapping)
    try:
        for part in route:
            route_here.append(part)
            scope = converter_func(scope)[part]
    except KeyError:
        raise ResourceLookupError(manager, route_here) from None
    return scope


@dataclasses.dataclass(frozen=True)
class ConfigAt(Generic[ConfigModelT]):
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

    owner: ConfigModelT
    mapping: dict[str, Any] | None
    route: SupportsRoute

    def get(self) -> Any:
        """
        Get the value of the item.

        Returns
        -------
        The value of the item.
        """
        try:
            scope = at(self.mapping or self.owner, self.route)
        except KeyError as err:
            route_here = err.args[1]
            raise ConfigAccessError(self.owner, route_here) from None
        return scope

    def update(self, value: Any) -> Any:
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
        route = list(Route(self.route))
        mapping = self.mapping or self.owner
        key = route.pop()
        submapping = _vars(mapping)
        route_here = []
        try:
            for part in route:
                route_here.append(part)
                submapping = _vars(submapping[part])
            submapping[key] = value
        except KeyError:
            raise ConfigAccessError(self.owner, route_here) from None
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


def save(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    write_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> int:
    """
    Save the configuration.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    write_kwargs
        Keyword arguments to pass to the writing function.
    **kwargs
        Keyword arguments to pass to the dumping function.

    Returns
    -------
    The number of bytes written.
    """
    if isinstance(section, ConfigModel):
        config = section
        return config.save(write_kwargs=write_kwargs, **kwargs)

    if write_kwargs is None:
        write_kwargs = {}

    config = section.owner
    data = config.initial_state
    scope = ConfigAt(config, data, section.route)
    data = scope.update(section.get())
    context = get_context(config)
    blob = context.manager.dump_config(config.copy(update=data), **kwargs)
    result = config.write(blob, **write_kwargs)
    context.initial_state = data
    return result


async def save_async(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    write_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> int:
    """
    Save the configuration asynchronously.

    Parameters
    ----------
    section
        The configuration model instance or the configuration item.
    write_kwargs
        Keyword arguments to pass to the writing function.
    **kwargs
        Keyword arguments to pass to the dumping function.

    Returns
    -------
    The number of bytes written.
    """
    if isinstance(section, ConfigModel):
        config = section
        return await config.save_async(write_kwargs=write_kwargs, **kwargs)

    if write_kwargs is None:
        write_kwargs = {}

    config = section.owner
    data = config.initial_state
    scope = ConfigAt(config, data, section.route)
    data = scope.update(section.get())
    context = get_context(config)
    blob = context.manager.dump_config(config.copy(update=data), **kwargs)
    result = await config.write_async(blob, **write_kwargs)
    context.initial_state = data
    return result


def reload(section: ConfigModelT | ConfigAt[ConfigModelT], **kwargs: Any) -> Any:
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
    state = config.__dict__
    newest = context.manager.read(config_class=type(config), **kwargs)
    section_data = ConfigAt(newest, newest.__dict__, section.route).get()
    ConfigAt(config, state, section.route).update(section_data)
    return section_data


async def reload_async(
    section: ConfigModelT | ConfigAt[ConfigModelT], **kwargs: Any
) -> Any:
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
    if isinstance(section, ConfigModel):
        config = section
        return await config.reload_async()

    config = section.owner
    context = get_context(config)
    state = config.__dict__
    newest = await context.manager.read_async(config_class=type(config), **kwargs)
    section_data = ConfigAt(newest, newest.__dict__, section.route).get()
    ConfigAt(config, state, section.route).update(section_data)
    return section_data


class BaseContext(abc.ABC, Generic[ConfigModelT]):
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

    @abc.abstractmethod
    def trace_route(self) -> collections.abc.Iterator[str]:
        """Trace the route to where the configuration subcontext points to."""

    @property
    def route(self) -> Route:
        """The route to where the configuration subcontext points to."""
        return Route(list(self.trace_route()))

    @overload
    def enter(self: BaseContext[ConfigModelT], part: None) -> BaseContext[ConfigModelT]:
        ...

    @overload
    def enter(self, part: str) -> Subcontext[ConfigModelT]:
        ...

    def enter(
        self, part: str | None
    ) -> Subcontext[ConfigModelT] | BaseContext[ConfigModelT]:
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
        if part is None:
            return self
        return Subcontext(self, part)

    @property
    @abc.abstractmethod
    def manager(self) -> ConfigManager[ConfigModelT]:
        """The configuration resource manager."""

    @property
    @abc.abstractmethod
    def owner(self) -> ConfigModelT | None:
        """
        The top-level configuration model instance,
        holding all adjacent contexts.
        """

    @property
    @abc.abstractmethod
    def at(self) -> ConfigModelT | ConfigAt[ConfigModelT] | None:
        """
        The configuration model instance or the configuration item
        this context points to.
        """


class Context(BaseContext[ConfigModelT], Generic[ConfigModelT]):
    """
    The context of a configuration model.

    Parameters
    ----------
    manager
        The configuration resource.
    owner
        The top-level configuration model instance,
        holding all belonging subcontexts.
    """

    _initial_state: dict[str, Any]

    def __init__(
        self,
        manager: ConfigManager[ConfigModelT],
        owner: ConfigModelT | None = None,
    ) -> None:
        self._manager = manager
        self._owner = None
        self._initial_state = {}

        self.owner = owner

    def trace_route(self) -> collections.abc.Iterator[str]:
        yield from ()

    @property
    def manager(self) -> ConfigManager[ConfigModelT]:
        return self._manager

    @property
    def at(self) -> ConfigModelT | None:
        return self.owner

    @property
    def owner(self) -> ConfigModelT | None:
        return self._owner

    @owner.setter
    def owner(self, config: ConfigModelT | None) -> None:
        if config is None:
            return
        self._owner = config

    @property
    def initial_state(self) -> dict[str, Any]:
        return copy.deepcopy(self._initial_state)

    @initial_state.setter
    def initial_state(self, initial_state: dict[str, Any]) -> None:
        self._initial_state = copy.deepcopy(initial_state)

    def __repr__(self) -> str:
        manager = self.manager
        return (
            f"<{type(self).__name__} "
            f"of {type(self.owner).__name__!r} configuration "
            f"({manager=})>"
        )


class Subcontext(BaseContext[ConfigModelT], Generic[ConfigModelT]):
    """
    The subcontext of a configuration model.

    Parameters
    ----------
    parent
        The parent context.
    part
        The name of the item nested in the item the parent context points to.
    """

    def __init__(self, parent: BaseContext[ConfigModelT], part: str) -> None:
        self._parent = parent
        self._part = part

    @property
    def manager(self) -> ConfigManager[ConfigModelT]:
        return self._parent.manager

    def trace_route(self) -> collections.abc.Iterator[str]:
        yield from self._parent.trace_route()
        yield self._part

    @property
    def at(self) -> ConfigAt[ConfigModelT]:
        if self.owner is None:
            msg = "Cannot get section pointed to by an unbound context"
            raise ValueError(msg)
        return ConfigAt(self.owner, None, self.route)

    @property
    def owner(self) -> ConfigModelT | None:
        return self._parent.owner

    @property
    def initial_state(self) -> dict[str, Any]:
        return self._parent.initial_state

    @initial_state.setter
    def initial_state(self, value: dict[str, Any]) -> None:
        data = self._parent.initial_state
        data[self._part] = copy.deepcopy(value)
        self._parent.initial_state = data

    def __repr__(self) -> str:
        manager = self.manager
        route = self.route
        return (
            f"<{type(self).__name__} "
            f"of {type(self.owner).__name__ + '.' + str(route)!r} configuration "
            f"({manager=})>"
        )


def get_context(config: ConfigModelT) -> BaseContext[ConfigModelT]:
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
    context = get_context_or_none(config)
    if context is None:
        raise RuntimeError("Cannot get context of unbound configuration model")
    return context


def get_context_or_none(config: ConfigModelT) -> BaseContext[ConfigModelT] | None:
    """
    Get the context of the configuration model safely.

    Parameters
    ----------
    config
        The configuration model instance.

    Returns
    -------
    The context of the configuration model.
    """
    return cast(
        Optional[BaseContext[ConfigModelT]], getattr(config, LOCAL).get(current_context)
    )


def _json_encoder(
    model_encoder: collections.abc.Callable[..., Any], value: Any, **kwargs: Any
) -> Any:
    initial_state_type = type(value)
    converted_value = pre_serialize(value)
    if isinstance(converted_value, initial_state_type):
        return model_encoder(value, **kwargs)
    return converted_value


class ConfigModelMetaclass(ModelMetaclass):
    def __new__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        namespace |= dict.fromkeys(
            (EXPORT, CONTEXT, LOCAL, TOKEN), pydantic.PrivateAttr()
        )

        if kwargs.pop("root", None):
            return type.__new__(cls, name, bases, namespace, **kwargs)

        new_class = super().__new__(cls, name, bases, namespace, **kwargs)
        for field in new_class.__fields__.values():
            if type(field.outer_type_) is ConfigModelMetaclass:
                if field.pre_validators is None:
                    field.pre_validators = []
                validator = make_generic_validator(
                    field.outer_type_.__field_setup__  # type: ignore[attr-defined]
                )
                field.pre_validators.insert(0, validator)
        new_class.__json_encoder__ = functools.partial(
            _json_encoder,
            new_class.__json_encoder__,
        )
        return cast(type, new_class)


class ConfigModel(
    pydantic.BaseSettings,
    metaclass=ConfigModelMetaclass,
    root=True,
):
    """
    The base class for configuration models.

    It is not recommended to inherit from this class directly for basic usage.
    Instead, use either :class:`ConfigModel` or :class:`AsyncConfigModel`.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Set private attributes via the constructor
        # to allow preprocessor-related instances to exist.
        missing = object()
        for private_attr in self.__private_attributes__:
            value = kwargs.pop(private_attr, missing)
            if value is not missing:
                setattr(self, private_attr, value)
                if private_attr == CONTEXT:
                    current_context.set(value)
        super().__init__(**kwargs)

    def _init_private_attributes(self) -> None:
        super()._init_private_attributes()
        local = contextvars.copy_context()
        setattr(self, LOCAL, local)
        tok = getattr(self, TOKEN, None)
        if tok:
            current_context.reset(tok)

    def export(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        tok = _exporting.set(True)  # noqa: FBT003
        ctx = contextvars.copy_context()
        _exporting.reset(tok)
        return ctx.run(self.dict, **kwargs)

    async def export_async(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        tok = _exporting.set(True)  # noqa: FBT003
        ctx = contextvars.copy_context()
        _exporting.reset(tok)
        return await ctx.run(self.dict_async, **kwargs)

    async def dict_async(self, **kwargs: Any) -> dict[str, Any]:
        """
        Get the dictionary representation of the configuration model.

        Returns
        -------
        The dictionary representation of the configuration model.
        """
        return dict(await self._iter_async(to_dict=True, **kwargs))

    async def json_async(  # noqa: PLR0913
        self,
        include: IncludeExcludeT = None,
        exclude: IncludeExcludeT = None,
        *,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        encoder: collections.abc.Callable[[Any], Any] | None = None,
        models_as_dict: bool = True,
        **dumps_kwargs: Any,
    ) -> str:
        encoder = cast(
            collections.abc.Callable[[Any], Any], encoder or self.__json_encoder__
        )
        data = dict(
            await self._iter_async(
                to_dict=models_as_dict,
                by_alias=by_alias,
                include=include,
                exclude=exclude,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )
        )
        if self.__custom_root_type__:
            data = data[ROOT_KEY]
        return self.__config__.json_dumps(data, default=encoder, **dumps_kwargs)

    def _iter(  # type: ignore[override]
        self, **kwargs: Any
    ) -> collections.abc.Iterator[tuple[str, Any]]:
        if kwargs.get("to_dict", False) and _exporting.get():
            state = {}
            for key, value in super()._iter(**kwargs):
                state[key] = value
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                context.manager.processor_class.export(state, metadata=metadata)
            yield from state.items()
        else:
            yield from super()._iter(**kwargs)

    async def _iter_async(
        self, **kwargs: Any
    ) -> collections.abc.Iterator[tuple[str, Any]]:
        if kwargs.get("to_dict", False) and _exporting.get():
            state = {}
            for key, value in super()._iter(**kwargs):
                state[key] = value
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                await context.manager.processor_class.export_async(
                    state, metadata=metadata
                )
            return ((key, value) for key, value in state.items())
        return super()._iter(**kwargs)

    @classmethod
    @no_type_check
    def _get_value(cls, value: Any, *, to_dict: bool, **kwds: Any) -> Any:
        if _exporting.get():
            exporter = export.dispatch(type(value))
            if (
                isinstance(value, BaseModel) or exporter != export.dispatch(object)
            ) and to_dict:
                value_dict = export(value, **kwds)
                if ROOT_KEY in value_dict:
                    return value_dict[ROOT_KEY]
                return value_dict
        return super()._get_value(value, to_dict=to_dict, **kwds)

    @classmethod
    def _resolve_manager(
        cls,
        resource: ConfigManager[ConfigModelT] | RawResourceT | None = None,
        *,
        create_if_missing: bool | None = None,
    ) -> ConfigManager[ConfigModelT]:
        if resource is None:
            resource = getattr(cls.__config__, "resource", None)
        if resource is None:
            raise ValueError("No resource specified")
        manager: ConfigManager[ConfigModelT]
        if isinstance(resource, ConfigManager):
            manager = resource
        else:
            manager = ConfigManager(resource)
        if create_if_missing is not None:
            manager.create_if_missing = create_if_missing
        return manager

    @property
    def initial_state(self) -> dict[str, Any]:
        """
        The initial configuration state.

        It is a copy of the configuration state
        at the last time of loading, reloading or saving.
        """
        return get_context(self).initial_state

    def at(
        self: ConfigModelT,
        route: SupportsRoute,
    ) -> ConfigAt[ConfigModelT]:
        """
        Lazily point to a specific item in the configuration.

        Parameters
        ----------
        route
            The access route to the item in this configuration.

        Returns
        -------
        The configuration accessor.
        """
        return ConfigAt(self, None, route)

    def update(self, **kwargs: Any) -> None:
        """
        Update the configuration with new values, in-place.

        Parameters
        ----------
        kwargs
            The new values to update the configuration with.

        Returns
        -------
        None
        """
        # Crucial difference to self.__dict__.update():
        # self.__dict__.update() would not trigger the validation
        # of the new values.
        for key, value in kwargs.items():
            setattr(self, key, value)

    def rollback(self) -> None:
        """
        Rollback the configuration to its initial state.

        Returns
        -------
        None
        """
        context = get_context(self)
        self.__dict__.update(context.initial_state)

    def __deepcopy__(
        self: ConfigModelT, memodict: dict[Any, Any] | None = None
    ) -> ConfigModelT:
        state = dict(self._iter(to_dict=False))
        state.pop(LOCAL, None)
        state.pop(TOKEN, None)
        return type(self)(**copy.deepcopy(state))

    @classmethod
    def load(
        cls: type[ConfigModelT],
        resource: ConfigManager[ConfigModelT] | RawResourceT | None = None,
        *,
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
        manager = cls._resolve_manager(resource, create_if_missing=create_if_missing)
        context = Context(manager)  # type: Context[ConfigModelT]
        current_context.set(context)
        local = contextvars.copy_context()
        config = manager.read(config_class=cls, **kwargs)
        setattr(config, LOCAL, local)
        context.owner = config
        context.initial_state = config.__dict__
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
        tok = current_context.set(get_context(context.owner))
        if context.owner is self:
            changed = context.manager.read(config_class=type(self), **kwargs)
        else:
            changed = reload(cast(ConfigAt[ConfigModelT], context.at), **kwargs)
        current_context.reset(tok)
        state = changed.__dict__
        context.initial_state = state
        self.update(**state)
        return self

    def save(
        self: ConfigModelT, write_kwargs: dict[str, Any] | None = None, **kwargs: Any
    ) -> int:
        """
        Save the configuration to the configuration file.

        Parameters
        ----------
        write_kwargs
            Keyword arguments to pass to the write method.
        **kwargs
            Keyword arguments to pass to the dumping method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.owner is self:
            if write_kwargs is None:
                write_kwargs = {}
            blob = context.manager.dump_config(self, **kwargs)
            result = self.write(blob, **write_kwargs)
            context.initial_state = self.__dict__
            return result
        return save(
            cast(ConfigAt[ConfigModelT], context.at),
            write_kwargs=write_kwargs,
            **kwargs,
        )

    def write(self, blob: str, **kwargs: Any) -> int:
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
        if context.manager.is_url:
            msg = "Writing to URLs is not yet supported"
            raise NotImplementedError(msg)
        return context.manager.write(blob, **kwargs)

    @classmethod
    async def load_async(
        cls: type[ConfigModelT],
        resource: ConfigManager[ConfigModelT] | RawResourceT | None,
        *,
        create_if_missing: bool | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration file asynchronously.
        To reload the configuration, use the `reload()` method.

        Parameters
        ----------
        resource
            The configuration resource.
        create_if_missing
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        manager = cls._resolve_manager(resource, create_if_missing=create_if_missing)
        context = Context(manager)  # type: Context[ConfigModelT]
        current_context.set(context)
        local = contextvars.copy_context()
        config = await manager.read_async(config_class=cls, **kwargs)
        setattr(config, LOCAL, local)
        context.owner = config
        return config

    async def reload_async(self: ConfigModelT, **kwargs: Any) -> ConfigModelT:
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
        tok = current_context.set(get_context(context.owner))
        if context.owner is self:
            changed = await context.manager.read_async(
                config_class=type(self), **kwargs
            )
        else:
            changed = await reload_async(
                cast(ConfigAt[ConfigModelT], context.at), **kwargs
            )
        current_context.reset(tok)
        state = changed.__dict__
        context.initial_state = state
        self.update(**state)
        return self

    async def save_async(
        self: ConfigModelT, write_kwargs: dict[str, Any] | None = None, **kwargs: Any
    ) -> int:
        """
        Save the configuration to the configuration file asynchronously.

        Parameters
        ----------
        write_kwargs
            Keyword arguments to pass to the write method.
        **kwargs
            Keyword arguments to pass to the dumping method.

        Returns
        -------
        The number of bytes written.
        """
        context = get_context(self)
        if context.owner is self:
            if write_kwargs is None:
                write_kwargs = {}
            tok = _exporting.set(True)  # noqa: FBT003
            ctx = contextvars.copy_context()
            _exporting.reset(tok)
            blob = await ctx.run(context.manager.dump_config_async, self, **kwargs)
            result = await self.write_async(blob, **write_kwargs)
            context.initial_state = self.__dict__
            return result
        return await save_async(
            cast(ConfigAt[ConfigModelT], context.at),
            write_kwargs=write_kwargs,
            **kwargs,
        )

    async def write_async(self, blob: str, **kwargs: Any) -> int:
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
        if context.manager.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        return await context.manager.write_async(blob, **kwargs)

    @classmethod
    def __field_setup__(cls, value: Any, field: ModelField) -> Any:
        context = current_context.get()
        if context is not None:
            subcontext = context.enter(field.name)
            tok = current_context.set(subcontext)
            vs = _vars(value)
            vs[TOKEN] = tok
            vs[LOCAL] = contextvars.copy_context()
        return value


class ConfigMeta(pydantic.BaseSettings.Config):
    """
    Meta-configuration for the `ConfigModel` class.

    Attributes
    ----------
    resource
        The configuration resource to read from/write to.

        If a string, it will be interpreted as a path to a file.

    And all other attributes from `pydantic.BaseSettings.Config`.
    """

    resource: ConfigManager[ConfigModel] | RawResourceT | None = None
    validate_assignment: bool = True

    Extra = pydantic.Extra
