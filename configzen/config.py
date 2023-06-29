"""
The main module of the configzen library.

This module provides an API to manage configuration files and resources
in a consistent way. It also provides tools to load and save configuration
files in various formats and within a number of advanced methods.

.. code-block:: python

    from configzen import ConfigModel, ConfigField, ConfigMeta

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
import ast
import asyncio
import collections.abc
import contextvars
import copy
import dataclasses
import functools
import io
import os
import pathlib
import re
import string
import urllib.parse
import urllib.request
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Literal,
    Optional,
    cast,
    get_origin,
    no_type_check,
    overload,
)

import anyconfig
import pydantic
from anyconfig.utils import filter_options, is_dict_like, is_list_like
from pydantic.fields import make_generic_validator  # type: ignore[attr-defined]
from pydantic.fields import ModelField, Undefined
from pydantic.json import ENCODERS_BY_TYPE
from pydantic.main import BaseModel, ModelMetaclass
from pydantic.utils import ROOT_KEY

from configzen._isolation import isolate, isolate_async, isolate_calls
from configzen.errors import ConfigAccessError
from configzen.errors import (
    ResourceLookupError,
    UnavailableParserError,
    UnspecifiedParserError,
)
from configzen.processor import EXPORT, DirectiveContext, Processor
from configzen.route import ConfigRoute
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
    "ConfigAgent",
    "ConfigModel",
    "ConfigMeta",
    "save",
    "save_async",
    "reload",
    "reload_async",
    "export_hook",
    "field_hook",
    "export_model",
    "export_model_async",
)

_URL_SCHEMES: set[str] = set(
    urllib.parse.uses_relative + urllib.parse.uses_netloc + urllib.parse.uses_params
) - {""}
CONTEXT: str = "__context__"
TOKEN: str = "__context_token__"
LOCAL: str = "__local__"
INTERPOLATION_TRACKER: str = "__interpolation_tracker__"

current_context: contextvars.ContextVar[
    BaseContext[Any] | None
] = contextvars.ContextVar("current_context", default=None)

current_interpolation_tracker: contextvars.ContextVar[
    dict[str, Any] | None
] = contextvars.ContextVar("current_interpolation_tracker", default=None)

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
            defaults[field.alias] = default
    return defaults


def _get_object_state(obj: Any) -> dict[str, Any]:
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
def export_hook(obj: Any) -> Any:
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
        return export_hook(dataclasses.asdict(obj))
    if _is_namedtuple(obj):
        return _export_namedtuple(obj)
    return obj


for obj_type, obj_encoder in ENCODERS_BY_TYPE.items():
    export_hook.register(obj_type, obj_encoder)

field_hook_registrars: Any = functools.singledispatch(lambda _cls, value: value)

if TYPE_CHECKING:

    class _FieldHookType:
        def __call__(self, cls: type[T], value: Any) -> Any:
            ...

        def register(
            self,
            cls: type[T],
            func: collections.abc.Callable[[type[T], Any], Any] | None = None,
        ) -> collections.abc.Callable[
            [collections.abc.Callable[[type[T], Any], Any]],
            collections.abc.Callable[[type[T] | Any, Any], Any],
        ]:
            ...

        def dispatch(
            self, cls: type[T]
        ) -> collections.abc.Callable[[type[T] | Any, Any], Any]:
            ...

    field_hook: _FieldHookType = _FieldHookType()

else:

    def field_hook(cls: type[Any], value: Any) -> Any:
        """
        Automatically registered pre-validator for values in fields
        where the outer type is `cls`.

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
        origin = get_origin(cls)
        try:
            if isinstance(value, origin or cls):
                return value
        except TypeError:
            return value
        if origin:
            cls = origin
        try:
            cast_func = field_hook_registrars.dispatch(cls)
        except KeyError:
            return value
        return cast_func(cls, value)

    field_hook.register = field_hook_registrars.register


@functools.singledispatch
def export_model(obj: Any, **kwargs: Any) -> dict[str, Any]:
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
async def export_model_async(obj: Any, **kwargs: Any) -> dict[str, Any]:
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


@export_hook.register(pathlib.PureWindowsPath)
def _export_windows_path(obj: pathlib.PureWindowsPath) -> str:
    """
    Convert a Windows path to a string.

    Parameters
    ----------
    obj
        The path to convert.

    Returns
    -------
    The converted path.
    """
    return obj.as_posix()


@export_hook.register(list)
def _export_list(obj: list[Any]) -> list[Any]:
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
    return [export_hook(item) for item in obj]


@export_hook.register(collections.abc.Mapping)
def _export_mapping(obj: collections.abc.Mapping[Any, Any]) -> dict[Any, Any]:
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
    return {k: export_hook(v) for k, v in obj.items()}


@functools.singledispatch
def _export_namedtuple(obj: tuple[Any, ...]) -> Any:
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
    # Initially I wanted it to be export(obj._asdict()), but
    # pydantic doesn't seem to be friends with custom NamedTuple-s.
    return export_hook(list(obj))


@field_hook.register(dict)
@field_hook.register(list)
def _eval_literals(cls: type[Any], value: Any) -> Any:
    """
    Load a dict/list using literal evaluation.
    Solves nested dictionaries problem in INI files.

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
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            # There might be some following validator, let it be like that
            return value
        else:
            return cls(data)
    return cls(value)


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
                    f"Option {key}={value!r} overlaps with "
                    f"defined {actual_key}={target[actual_key]!r}"
                )
                raise ValueError(msg)
            target[actual_key] = value


class ConfigAgent(Generic[ConfigModelT]):
    """
    Configuration resource agent: loader and saver.

    This class is used to broke between the model and the home resource, which
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
    create_if_missing: bool
    is_relative: bool = False
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
    EXPORT_KWARGS: ClassVar[set[str]] = {
        "by_alias",
        "include",
        "exclude",
        "exclude_unset",
        "exclude_defaults",
        "exclude_none",
    }
    ALIAS_FILE_EXTENSIONS: ClassVar[dict[str, str]] = {
        "yml": "yaml",
        "conf": "ini",
        "cfg": "ini",
        # Note: CBOR (RFC 7049) is deprecated, use CBOR (RFC 8949) instead.
        "cbor": "cbor2",
        # https://github.com/msgpack/msgpack/issues/291#issuecomment-1370526984
        "mpk": "msgpack",
        "pkl": "pickle",
    }
    BINARY_AC_PARSERS: ClassVar[set[str]] = {
        "ion",
        "bson",
        "cbor",
        "cbor2",
        "msgpack",
        "pickle",
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
        uses_binary_data
            Whether to treat the data as binary. Defaults to True for BSON,
            CBOR, MessagePack and Pickle formats.
        **kwargs
            Additional keyword arguments to pass to
            `anyconfig.loads()` and `anyconfig.dumps()`.
        """
        self._ac_parser = None
        self._uses_binary_data = kwargs.get("uses_binary_data", False)

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
            if (
                raw_path.startswith(".")
                and resource.parts
                and not resource.parts[0].startswith(".")
            ):
                self.is_relative = True

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
        if self.ac_parser is None:
            self.ac_parser = self._guess_ac_parser()

    @property
    def ac_parser(self) -> str | None:
        return self._ac_parser

    @ac_parser.setter
    def ac_parser(self, value: str | None) -> None:
        if value is not None:
            value = value.casefold()
        self._ac_parser = value

    def _guess_ac_parser(self) -> str | None:
        ac_parser = None
        if isinstance(self.resource, pathlib.Path):
            suffix = self.resource.suffix[1:].casefold()
            supported_parsers = anyconfig.list_types()
            if not suffix:
                recognized_file_extensions = supported_parsers + [
                    alias + "(-> " + actual_parser + ")"
                    for alias, actual_parser in self.ALIAS_FILE_EXTENSIONS.items()
                    if actual_parser in supported_parsers
                ]
                msg = (
                    "Could not guess the anyconfig parser to use for "
                    f"{self.resource!r}.\n"
                    f"Recognized file extensions: {recognized_file_extensions}"
                )
                raise UnspecifiedParserError(msg)
            ac_parser = self.ALIAS_FILE_EXTENSIONS.get(suffix, suffix)
            if (
                ac_parser == "cbor2"
                and "cbor2" not in supported_parsers
                and "cbor" in supported_parsers
            ):
                ac_parser = "cbor"
        return ac_parser

    def load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str | bytes,
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
        dict_config = self.load_dict(blob, ac_parser=ac_parser, **kwargs)
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    async def async_load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str | bytes,
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
        dict_config = await self.load_dict_async(blob, ac_parser=ac_parser, **kwargs)
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    def _load_dict_impl(
        self,
        blob: str | bytes,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ac_parser = ac_parser or self.ac_parser or self._guess_ac_parser()
        if ac_parser is None:
            msg = "Cannot read configuration because `ac_parser` was not specified"
            raise UnspecifiedParserError(msg)
        kwargs = self.load_options | kwargs
        try:
            loaded = anyconfig.loads(  # type: ignore[no-untyped-call]
                blob, ac_parser=ac_parser, **kwargs
            )
        except anyconfig.UnknownParserTypeError as exc:
            raise UnavailableParserError(str(exc).split()[-1], self) from exc
        if not isinstance(loaded, collections.abc.Mapping):
            msg = (
                f"Expected a mapping as a result of loading {self.resource}, "
                f"got {type(loaded).__name__}."
            )
            raise TypeError(msg)
        return dict(loaded)

    def load_dict(
        self,
        blob: str | bytes,
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
        loaded = self._load_dict_impl(blob, ac_parser=ac_parser, **kwargs)
        if preprocess:
            loaded = self.processor_class(self, loaded).preprocess()
        return loaded

    async def load_dict_async(
        self,
        blob: str | bytes,
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
        loaded = self._load_dict_impl(blob, ac_parser, **kwargs)
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
        export_kwargs = filter_options(self.EXPORT_KWARGS, kwargs)
        if ac_parser == "json" and self.use_pydantic_json:
            export_kwargs |= filter_options(
                self.JSON_KWARGS, self.dump_options | kwargs
            )
            _exporting.set(True)  # noqa: FBT003
            return isolate(config.json, **export_kwargs)
        data = export_model(config, **export_kwargs)
        return self.dump_data(data, ac_parser=ac_parser, **kwargs)

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
        export_kwargs = filter_options(self.EXPORT_KWARGS, kwargs)
        if ac_parser == "json" and self.use_pydantic_json:
            export_kwargs |= filter_options(
                self.JSON_KWARGS, self.dump_options | kwargs
            )
            _exporting.set(True)  # noqa: FBT003
            return await isolate_async(config.json_async, **export_kwargs)
        data = await export_model_async(config, **export_kwargs)
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
        if ac_parser is None:
            msg = (
                "Cannot write configuration because `ac_parser` was not specified"
                f"for agent {self}"
            )
            raise UnspecifiedParserError(msg)
        kwargs = self.dump_options | kwargs
        return anyconfig.dumps(export_hook(data), ac_parser=ac_parser, **kwargs)

    @property
    def is_url(self) -> bool:
        """
        Whether the resource is a URL.

        This simply checks if the resource object is a string, since local paths
        are converted into `pathlib.Path` objects.
        """
        return isinstance(self.resource, str)

    @property
    def uses_binary_data(self) -> bool:
        """
        Whether the resource uses bytes for storing data, not str.
        """
        return self._uses_binary_data or self.ac_parser in self.BINARY_AC_PARSERS

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
            if self.uses_binary_data:
                return io.BytesIO()
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
            msg = "Asynchronous URL opening is not supported"
            raise NotImplementedError(msg)
        if not AIOFILES_AVAILABLE:
            msg = (
                "Aiofiles is not available, cannot open file "
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
                new_kwargs.setdefault("mode", "rb" if self.uses_binary_data else "r")
            elif method == "write":
                new_kwargs.setdefault("mode", "wb" if self.uses_binary_data else "w")
            else:
                msg = f"Invalid resource access method: {method!r}"
                raise ValueError(msg)
        if self.uses_binary_data:
            new_kwargs.pop("encoding", None)
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
            if self.create_if_missing:
                defaults = _get_defaults_from_model_class(config_class)
                blob = self.dump_data(defaults)
                self.write(blob, **(create_kwargs or {}))
            else:
                raise
        return self.load_into(config_class, blob, **self.load_options)

    def write(self, blob: str | bytes, **kwargs: Any) -> int:
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
            return fp.write(cast(str, blob))

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
        blob: str | bytes,
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
            # Technically those might be also bytes,
            # todo(bswck): type it properly
            return await fp.write(cast(str, blob))

    @classmethod
    def from_directive_context(
        cls,
        ctx: DirectiveContext,
        /,
        route_separator: str = ":",
        route_class: type[ConfigRoute] | None = None,
    ) -> tuple[ConfigAgent[ConfigModelT], SupportsRoute | None]:
        """
        Create a configuration agent from a preprocessor directive context.
        Return an optional scope that the context points to.

        Parameters
        ----------
        route_class
        route_separator
        ctx

        Returns
        -------
        The configuration agent.
        """
        if route_class is None:
            route_class = ConfigRoute
        route: SupportsRoute | None = None
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        if isinstance(ctx.snippet, str):
            path, _, route = ctx.snippet.partition(route_separator)
            route = ConfigRoute(
                route.strip().replace(route_separator, route_class.TOK_DOT)
            )
            args.append(path)
        elif isinstance(ctx.snippet, int):
            args.append(ctx.snippet)
        elif is_dict_like(ctx.snippet):
            kwargs |= ctx.snippet
        elif is_list_like(ctx.snippet):
            args += list(ctx.snippet)
        else:
            msg = (
                f"Invalid snippet for the {ctx.directive!r} directive: {ctx.snippet!r}"
            )
            raise ValueError(msg)
        return cls(*args, **kwargs), str(route)

    @classmethod
    def register_file_extension(
        cls,
        file_extension: str,
        *,
        ac_parser: str,
    ) -> None:
        """
        Register a file extension with the proper anyconfig parser to use.

        Parameters
        ----------
        file_extension
        ac_parser

        Returns
        -------
        """
        cls.ALIAS_FILE_EXTENSIONS[file_extension] = ac_parser

    def __repr__(self) -> str:
        resource = self.resource
        return f"{type(self).__name__}({resource=!r})"


def at(
    mapping: Any,
    route: SupportsRoute,
    converter_func: collections.abc.Callable[[Any], dict[str, Any]] = _get_object_state,
    agent: ConfigAgent[ConfigModelT] | None = None,
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
    agent

    Returns
    -------
    The item at the route.
    """
    route = ConfigRoute(route)
    route_here = []
    scope = _get_object_state(mapping)
    try:
        for part in route:
            route_here.append(part)
            scope = converter_func(scope)[part]
    except KeyError:
        raise ResourceLookupError(agent, route_here) from None
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

    def get(self, route: SupportsRoute | None = None, default: Any = Undefined) -> Any:
        """
        Get the value of the item.

        Parameters
        ----------
        route
            The route to the item. If not given, the sole route of this item is used.
            If given, the route is appended to the sole route of this item.
        default
            The default value to return if the item is not found.

        Returns
        -------
        The value of the item.
        """
        base_route = ConfigRoute(self.route)
        if route is None:
            route = base_route
        else:
            route = base_route.enter(ConfigRoute(route, allow_empty=True))
        try:
            scope = at(self.mapping or self.owner, route)
        except ResourceLookupError as err:
            if default is Undefined:
                route_here = err.route
                raise ConfigAccessError(self.owner, route_here) from None
            scope = default
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
        route = list(ConfigRoute(self.route))
        mapping = self.mapping or self.owner
        key = route.pop()
        scope = _get_object_state(mapping)
        route_here = []
        try:
            for part in route:
                route_here.append(part)
                scope = _get_object_state(scope[part])
            scope[key] = value
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
    blob = context.agent.dump_config(config.copy(update=data), **kwargs)
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
    blob = context.agent.dump_config(config.copy(update=data), **kwargs)
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
    newest = context.agent.read(config_class=type(config), **kwargs)
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
    newest = await context.agent.read_async(config_class=type(config), **kwargs)
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
    def route(self) -> ConfigRoute:
        """The route to where the configuration subcontext points to."""
        return ConfigRoute(list(self.trace_route()))

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
    def agent(self) -> ConfigAgent[ConfigModelT]:
        """The configuration agent responsible for loading and saving."""

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
    agent
        The configuration resource agent.
    owner
        The top-level configuration model instance,
        holding all belonging subcontexts.
    """

    _initial_state: dict[str, Any]

    def __init__(
        self,
        agent: ConfigAgent[ConfigModelT],
        owner: ConfigModelT | None = None,
    ) -> None:
        self._agent = agent
        self._owner = None
        self._initial_state = {}

        self.owner = owner

    def trace_route(self) -> collections.abc.Iterator[str]:
        yield from ()

    @property
    def agent(self) -> ConfigAgent[ConfigModelT]:
        return self._agent

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
        agent = self.agent
        return (
            f"<{type(self).__name__} "
            f"of {type(self.owner).__name__!r} configuration "
            f"({agent=})>"
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
    def agent(self) -> ConfigAgent[ConfigModelT]:
        return self._parent.agent

    def trace_route(self) -> collections.abc.Iterator[str]:
        yield from self._parent.trace_route()
        yield self._part

    @property
    def at(self) -> ConfigAt[ConfigModelT]:
        if self.owner is None:
            msg = "Cannot get at() of a model without parent model"
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
        agent = self.agent
        route = self.route
        return (
            f"<{type(self).__name__} "
            f"of {type(self.owner).__name__ + '.' + str(route)!r} configuration "
            f"({agent=})>"
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
        raise RuntimeError(
            "This model is either inside a list "
            "or was not loaded by a configuration agent."
        )
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


# noinspection PyUnusedLocal
@make_generic_validator
def _common_field_validator(
    cls: type[ConfigModelT],
    v: Any,
    values: dict[str, Any],
    field: pydantic.fields.ModelField,
    config: pydantic.BaseConfig,
) -> Any:
    post_hook_value = field_hook(field.outer_type_, v)
    disallow_interpolation = getattr(config, "disallow_interpolation", False)
    disallowed_interpolation_fields = set()

    interpolation_tracker = current_interpolation_tracker.get()

    if interpolation_tracker is None:
        interpolation_tracker = {}
        current_interpolation_tracker.set(interpolation_tracker)

    if not isinstance(disallow_interpolation, bool):
        disallowed_interpolation_fields = set(disallow_interpolation)
    if (
        field.field_info.extra.get("interpolate", True)
        and field.alias not in disallowed_interpolation_fields
    ):
        old_value = post_hook_value
        new_value = interpolate(
            post_hook_value, cls.get_interpolation_context() | values
        )
        if old_value != new_value:
            interpolation_tracker[field.alias] = (old_value, copy.copy(new_value))
        post_hook_value = new_value
    return post_hook_value


def _json_encoder(
    model_encoder: collections.abc.Callable[..., Any], value: Any, **kwargs: Any
) -> Any:
    initial_state_type = type(value)
    converted_value = export_hook(value)
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
        namespace[INTERPOLATION_TRACKER] = pydantic.PrivateAttr(default_factory=dict)

        if kwargs.pop("root", None):
            return type.__new__(cls, name, bases, namespace, **kwargs)

        model = super().__new__(cls, name, bases, namespace, **kwargs)
        for field in model.__fields__.values():
            if field.pre_validators is None:
                field.pre_validators = []
            field.pre_validators[:] = [_common_field_validator, *field.pre_validators]
            if type(field.outer_type_) is ConfigModelMetaclass:
                validator = make_generic_validator(
                    field.outer_type_.__field_setup__  # type: ignore[attr-defined]
                )
                field.pre_validators[:] = [
                    _common_field_validator,
                    validator,
                    *field.pre_validators,
                ]
        model_encoder = model.__json_encoder__
        model.__json_encoder__ = functools.partial(_json_encoder, model_encoder)
        return cast(type, model)


class ConfigInterpolationTemplate(string.Template):
    idpattern = r"(?a:[_a-z][\\\.\[\]_a-z0-9]*)"
    flags = re.IGNORECASE | re.UNICODE


@functools.singledispatch
def interpolate(value: Any, _ctx: dict[str, Any]) -> Any:
    return value


@interpolate.register(str)
def _interpolate_str(value: str, ctx: dict[str, Any]) -> str:
    template = ConfigInterpolationTemplate(value)
    return template.safe_substitute(ctx)


# Interpolation for mutable types is a non-trivial task.

# @interpolate.register(dict)
# def _interpolate_dict(value: dict[Any, Any], ctx: dict[str, Any]) -> dict[Any, Any]:
#     return {
#         interpolate(k, ctx): interpolate(v, ctx) for k, v in value.items()
#     }
#
#
# @interpolate.register(list)
# def _interpolate_lst(value: list, ctx: dict[str, Any]) -> list[Any]:
#     return {
#         interpolate(k, ctx): interpolate(v, ctx) for k, v in value.items()
#     }


class ConfigMeta(pydantic.BaseSettings.Config):
    """
    Meta-configuration for configuration models.

    See https://docs.pydantic.dev/latest/usage/model_config/ for more information
    on model configurations.

    Attributes
    ----------
    resource
        The configuration resource to read from/write to.

        If a string, it will be interpreted as a path to a file.

    ac_parser
        The anyconfig parser to use.

    autoupdate_forward_refs
        Whether to automatically update forward references
        when `ConfigModel.load()` or `ConfigModel.load_async()`
        methods are called. For convenience, defaults to `True`.

    And all other attributes from `pydantic.BaseSettings.Config`.
    """

    resource: ConfigAgent[ConfigModel] | RawResourceT | None = None
    ac_parser: str | None = None
    validate_assignment: bool = True
    autoupdate_forward_refs: bool = True

    Extra = pydantic.Extra


class ConfigModel(
    pydantic.BaseSettings,
    metaclass=ConfigModelMetaclass,
    root=True,
):
    """The base class for configuration models."""

    __config__ = ConfigMeta

    def __init__(self, **kwargs: Any) -> None:
        # Set private attributes via the constructor
        # to allow preprocessor-related instances to exist.
        for private_attr in self.__private_attributes__:
            value = kwargs.pop(private_attr, Undefined)
            if value is not Undefined:
                if private_attr == CONTEXT:
                    context = current_context.get()
                    if context:
                        value = context
                    current_context.set(value)
                object.__setattr__(self, private_attr, value)
        super().__init__(**kwargs)

    def __deepcopy__(
        self: ConfigModelT, memodict: dict[Any, Any] | None = None
    ) -> ConfigModelT:
        state = dict(self._iter(to_dict=False))
        state.pop(LOCAL, None)
        state.pop(TOKEN, None)
        clone = copy.deepcopy(state)
        return type(self).parse_obj(
            {
                field.alias: clone[field_name]
                for field_name, field in self.__fields__.items()
            }
        )

    def __setattr__(self, key: str, value: Any) -> None:
        getattr(self, LOCAL).run(super().__setattr__, key, value)

    def _init_private_attributes(self) -> None:
        super()._init_private_attributes()
        local = contextvars.copy_context()
        object.__setattr__(self, LOCAL, local)
        tok = getattr(self, TOKEN, None)
        if tok:
            current_context.reset(tok)

    @isolate_calls
    def export(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        _exporting.set(True)  # noqa: FBT003
        return isolate(self.dict, **kwargs)

    @isolate_calls
    async def export_async(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        _exporting.set(True)  # noqa: FBT003
        return await isolate_async(self.dict_async, **kwargs)

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
            state: dict[str, Any] = {}
            for key, value in super()._iter(**kwargs):
                state |= [self._iter_hook(key, value)]
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                context.agent.processor_class.export(state, metadata=metadata)
            yield from state.items()
        else:
            yield from super()._iter(**kwargs)

    async def _iter_async(
        self, **kwargs: Any
    ) -> collections.abc.Iterator[tuple[str, Any]]:
        if kwargs.get("to_dict", False) and _exporting.get():
            state: dict[str, Any] = {}
            for key, value in super()._iter(**kwargs):
                state |= [self._iter_hook(key, value)]
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                await context.agent.processor_class.export_async(
                    state, metadata=metadata
                )
            return ((key, value) for key, value in state.items())
        return super()._iter(**kwargs)

    def _iter_hook(
        self,
        key: str,
        value: Any,
    ) -> tuple[str, Any]:
        interpolation_tracker = getattr(self, LOCAL).get(current_interpolation_tracker)
        field = self.__fields__.get(key)
        actual_key = field.alias if field else key
        interpolation_track = interpolation_tracker.get(actual_key)
        if interpolation_track:
            old_value, new_value = interpolation_track
            # if value != new_value:
            #     raise InterpolationError(
            #         f"Cannot restore the value of {actual_key!r} "
            #         "before interpolation."
            #     )
            value = old_value
        return actual_key, value

    @classmethod
    @no_type_check
    def _get_value(cls, value: Any, *, to_dict: bool, **kwds: Any) -> Any:
        if _exporting.get():
            exporter = export_model.dispatch(type(value))
            if (
                isinstance(value, BaseModel)
                or exporter != export_model.dispatch(object)
            ) and to_dict:
                value_dict = export_model(value, **kwds)
                if ROOT_KEY in value_dict:
                    return value_dict[ROOT_KEY]
                return value_dict
        return super()._get_value(value, to_dict=to_dict, **kwds)

    @classmethod
    def _resolve_agent(
        cls,
        resource: ConfigAgent[ConfigModelT] | RawResourceT | None = None,
        *,
        create_if_missing: bool | None = None,
        ac_parser: str | None = None,
    ) -> ConfigAgent[ConfigModelT]:
        if resource is None:
            resource = getattr(cls.__config__, "resource", None)
        if resource is None:
            raise ValueError("No resource specified")
        if ac_parser is None:
            ac_parser = getattr(cls.__config__, "ac_parser", None)
        agent: ConfigAgent[ConfigModelT]
        if isinstance(resource, ConfigAgent):
            agent = resource
        else:
            agent = ConfigAgent(
                resource,
                ac_parser=ac_parser,
            )
        if create_if_missing is not None:
            agent.create_if_missing = create_if_missing
        if ac_parser is not None:
            agent.ac_parser = cast(str, ac_parser)
        return agent

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
        route: SupportsRoute | None = None,
    ) -> ConfigModelT | ConfigAt[ConfigModelT]:
        """
        Lazily point to a specific item in the configuration.

        Parameters
        ----------
        route
            The access route to the item in this configuration.
            If None, the whole configuration is returned.

        Returns
        -------
        The configuration accessor.
        """
        if route is None:
            context = get_context_or_none(self)
            self_at = None
            if context is not None:
                self_at = context.at
            if self_at is not None:
                return self_at
            return self
        return ConfigAt(self, None, route)

    @overload
    def get(self: ConfigModelT, route: None = None, default: Any = ...) -> ConfigModelT:
        ...

    @overload
    def get(self: ConfigModelT, route: SupportsRoute = ..., default: Any = ...) -> Any:
        ...

    def get(self, route: SupportsRoute | None = None, default: Any = Undefined) -> Any:
        """
        Get a value from the configuration.

        Parameters
        ----------
        route
            Route to access the item. If None, the whole configuration is returned.
        default
        """
        if route is None:
            return self
        return self.at(route).get(default=default)

    def update(self, kwargs: dict[str, Any]) -> None:
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

    @classmethod
    @isolate_calls
    def load(
        cls: type[ConfigModelT],
        resource: ConfigAgent[ConfigModelT] | RawResourceT | None = None,
        *,
        create_if_missing: bool | None = None,
        ac_parser: str | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration file.
        To reload the configuration, use the `reload()` method.

        Parameters
        ----------
        resource
            The configuration resource to read from/write to.
        ac_parser
            The anyconfig parser to use.
        create_if_missing
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        agent = cls._resolve_agent(
            resource,
            ac_parser=ac_parser,
            create_if_missing=create_if_missing,
        )
        current_context.set(Context(agent))
        local = contextvars.copy_context()
        if getattr(
            cls.__config__,
            "autoupdate_forward_refs",
            ConfigMeta.autoupdate_forward_refs,
        ):
            cls.update_forward_refs()
        config = local.run(agent.read, config_class=cls, **kwargs)
        object.__setattr__(config, LOCAL, local)
        context = cast(Context[ConfigModelT], local.get(current_context))
        context.owner = config
        context.initial_state = config.__dict__
        return config

    @isolate_calls
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
        current_context.set(get_context(context.owner))
        if context.owner is self:
            changed = context.agent.read(config_class=type(self), **kwargs)
        else:
            changed = reload(cast(ConfigModelT, context.at), **kwargs)
        state = changed.__dict__
        context.initial_state = state
        self.update(state)
        return self

    @isolate_calls
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
            blob = context.agent.dump_config(self, **kwargs)
            result = self.write(blob, **write_kwargs)
            context.initial_state = self.__dict__
            return result
        return save(
            cast(ConfigAt[ConfigModelT], context.at),
            write_kwargs=write_kwargs,
            **kwargs,
        )

    def write(self, blob: str | bytes, **kwargs: Any) -> int:
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
        if context.agent.is_url:
            msg = "Writing to URLs is not yet supported"
            raise NotImplementedError(msg)
        return context.agent.write(blob, **kwargs)

    @classmethod
    @isolate_calls
    async def load_async(
        cls: type[ConfigModelT],
        resource: ConfigAgent[ConfigModelT] | RawResourceT | None = None,
        *,
        ac_parser: str | None = None,
        create_if_missing: bool | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration file asynchronously.
        To reload the configuration, use the `reload_async()` method.

        Parameters
        ----------
        resource
            The configuration resource.
        ac_parser
            The anyconfig parser to use.
        create_if_missing
            Whether to create the configuration file if it does not exist.
        **kwargs
            Keyword arguments to pass to the read method.

        Returns
        -------
        self
        """
        agent = cls._resolve_agent(
            resource, create_if_missing=create_if_missing, ac_parser=ac_parser
        )
        current_context.set(Context(agent))
        local = contextvars.copy_context()
        if getattr(
            cls.__config__,
            "autoupdate_forward_refs",
            ConfigMeta.autoupdate_forward_refs,
        ):
            cls.update_forward_refs()
        task = local.run(
            asyncio.create_task, agent.read_async(config_class=cls, **kwargs)
        )
        config = await task
        object.__setattr__(config, LOCAL, local)
        context = cast(Context[ConfigModelT], local.get(current_context))
        context.owner = config
        return config

    @isolate_calls
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
        current_context.set(get_context(context.owner))
        if context.owner is self:
            changed = await context.agent.read_async(config_class=type(self), **kwargs)
        else:
            changed = await reload_async(
                cast(ConfigAt[ConfigModelT], context.at), **kwargs
            )
        state = changed.__dict__
        context.initial_state = state
        self.update(state)
        return self

    @isolate_calls
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
            _exporting.set(True)  # noqa: FBT003
            blob = await context.agent.dump_config_async(self, **kwargs)
            result = await self.write_async(blob, **write_kwargs)
            context.initial_state = self.__dict__
            return result
        return await save_async(
            cast(ConfigAt[ConfigModelT], context.at),
            write_kwargs=write_kwargs,
            **kwargs,
        )

    async def write_async(self, blob: str | bytes, **kwargs: Any) -> int:
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
        if context.agent.is_url:
            msg = "Saving to URLs is not yet supported"
            raise NotImplementedError(msg)
        return await context.agent.write_async(blob, **kwargs)

    @staticmethod
    def get_interpolation_context() -> dict[str, Any]:
        """
        Get the interpolation context of this configuration model.
        This must be either static or class method.
        """
        return {}

    @classmethod
    def __field_setup__(cls, value: dict[str, Any], field: ModelField) -> Any:
        """
        Called when this configuration model is being initialized as a field
        of some other configuration model.
        """
        context = current_context.get()
        if context is not None:
            subcontext = context.enter(field.name)
            tok = current_context.set(subcontext)
            return _get_object_state(value) | {
                TOKEN: tok,
                LOCAL: contextvars.copy_context(),
            }
        return value


@field_hook.register(ConfigModel)
def _eval_model(cls: type[ConfigModelT], value: Any) -> ConfigModelT | Any:
    """
    Load a model using dict literal evaluation.
    Solves nested dictionaries problem in INI files.

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
    data = value
    if isinstance(value, str):
        try:
            data = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            # Note: Strings resembling models is probably not intended
            # to be used with automatic pickle/JSON parsing.
            # return cls.parse_raw(value)
            return data
        else:
            return cls.parse_obj(data)
    return data
