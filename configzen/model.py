"""
The main module of the configzen library.

This module provides an API to manage configuration files and resources
in a consistent way. It also provides tools to load and save configuration
files in various formats and within a number of advanced methods.

```python
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
```
"""
# pyright: reportInvalidTypeVarUse=false, reportGeneralTypeIssues=false

from __future__ import annotations

import abc
import asyncio
import contextvars
import copy
import dataclasses
import functools
import importlib
import inspect
import io
import itertools
import os
import pathlib
import sys
import types
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Literal,
    Optional,
    Union,
    cast,
    get_args,
    get_origin,
    no_type_check,
    overload,
)

import anyconfig
import pydantic
from anyconfig.utils import filter_options, is_dict_like, is_list_like
from pydantic.class_validators import make_generic_validator
from pydantic.fields import ModelField, Undefined
from pydantic.main import BaseModel, ModelMetaclass
from pydantic.utils import ROOT_KEY

from configzen._detach import (
    detached_context_await,
    detached_context_function,
    detached_context_run,
)
from configzen.errors import (
    ConfigAccessError,
    InterpolationError,
    ResourceLookupError,
    UnavailableParserError,
    UnspecifiedParserError,
)
from configzen.interpolation import (
    EVALUATION_ENGINE,
    INTERPOLATOR,
    BaseEvaluationEngine,
    BaseInterpolator,
    include,
    include_const,
    interpolate,
)
from configzen.module import MODULE, ConfigModule
from configzen.processor import EXPORT, DirectiveContext, Processor
from configzen.route import ConfigRoute
from configzen.typedefs import (
    AsyncConfigIO,
    ConfigIO,
    ConfigModelT,
    ConfigRouteLike,
    IncludeExcludeT,
    NormalizedResourceT,
    RawResourceT,
    T,
)

try:
    import aiofiles
except ImportError:
    aiofiles = None  # type: ignore[assignment]

__all__ = (
    "ConfigAgent",
    "ConfigAt",
    "ConfigModel",
    "ConfigMeta",
    "export_hook",
    "field_hook",
    "export_model",
    "export_model_async",
)

ALL_URL_SCHEMES: set[str] = set(
    urllib.parse.uses_relative + urllib.parse.uses_netloc + urllib.parse.uses_params,
) - {""}

CONTEXT: str = "__context__"
TOKEN: str = "__context_token__"
LOCAL: str = "__local__"

INTERPOLATION_TRACKER: str = "__interpolation_tracker__"
INTERPOLATION_INCLUSIONS: str = "__interpolation_inclusions__"

current_context: contextvars.ContextVar[
    BaseContext[Any] | None
] = contextvars.ContextVar("current_context", default=None)

current_interpolation_tracker: contextvars.ContextVar[
    dict[str, Any] | None
] = contextvars.ContextVar("current_interpolation_tracker", default=None)

_exporting: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_exporting",
    default=False,
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
    state = obj
    if not isinstance(obj, dict):
        state = obj.__dict__  # avoidance of vars() is intended
    return cast("dict[str, Any]", state)


@functools.singledispatch
def export_hook(obj: Any) -> Any:
    """
    Convert a value to a format that can be safely serialized & deserialized.

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
    return obj


field_hook_registrars: Any = functools.singledispatch(lambda _cls, value: value)

if TYPE_CHECKING:

    class _FieldHookType(Generic[T]):
        def __call__(self, cls: type[T], value: Any) -> Any:
            ...

        def register(
            self,
            cls: type[T],
            func: Callable[[type[T], Any], Any] | None = None,
        ) -> Callable[
            [Callable[[type[T], Any], Any]],
            Callable[[type[T] | Any, Any], Any],
        ]:
            ...

        def dispatch(self, cls: type[T]) -> Callable[[type[T] | Any, Any], Any]:
            ...

    field_hook: _FieldHookType[Any] = _FieldHookType()

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
        if origin in [Union] + (
            [types.UnionType] if sys.version_info >= (3, 10) else []
        ):
            for result in itertools.starmap(
                field_hook,
                zip(get_args(cls), itertools.repeat(value)),
            ):
                if result != value:
                    return result
            return value
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
        The model to export.
    **kwargs
        Additional keyword arguments to pass to `obj.dict()`.
    """
    if isinstance(obj, ConfigModel) and not _exporting.get():
        return obj.export(**kwargs)
    return cast("dict[str, Any]", obj.dict(**kwargs))


@functools.singledispatch
async def export_model_async(obj: Any, **kwargs: Any) -> dict[str, Any]:
    """
    Export a ConfigModel to a safely-serializable format.
    Register a custom exporter for a type using the `with_exporter` decorator,
    which can help to exclude particular values from the export if needed.

    Parameters
    ----------
    obj
        The model to export.
    **kwargs
        Additional keyword arguments to pass to `obj.dict()`.
    """
    if isinstance(obj, ConfigModel) and not _exporting.get():
        return await obj.export_async(**kwargs)
    return cast("dict[str, Any]", await obj.dict_async(**kwargs))


def _delegate_ac_options(
    load_options: dict[str, Any],
    dump_options: dict[str, Any],
    options: dict[str, Any],
    *,
    dump_prefix: str = "dump_",
    load_prefix: str = "load_",
) -> None:
    for key, value in options.items():
        if key.startswith(dump_prefix):
            actual_key = key[len(dump_prefix) :]  #  key.removeprefix(dump_prefix)
            targets = [dump_options]
        elif key.startswith(load_prefix):
            actual_key = key[len(load_prefix) :]  #  key.removeprefix(load_prefix)
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

    Attributes
    ----------
    create_if_missing
        Whether to create the file if it doesn't exist.
    parser_name
        The name of the engines to use for loading and saving the
        configuration. If not specified, the processor will be guessed
        from the file extension.
    allowed_url_schemes
        The URL schemes that are allowed to be used.

    Raises
    ------
    ValueError
    """

    processor_class: type[Processor[ConfigModelT]]
    create_if_missing: bool
    is_relative: bool = False
    allowed_url_schemes: set[str]
    use_pydantic_json: bool = True
    default_load_options: ClassVar[dict[str, Any]] = {}
    default_dump_options: ClassVar[dict[str, Any]] = {
        # These are usually desirable for configuration files.
        # If you want to change them, you can do so by monkey-patching
        # these variables. You can also change `load_options` and
        # `dump_options` instance attributes to make a local change.
        "allow_unicode": True,
        "ensure_ascii": False,
        "indent": 2,
    }
    _resource: NormalizedResourceT

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
    EXTRA_FILE_EXTENSIONS: ClassVar[dict[str, str]] = {
        "yml": "yaml",
        "conf": "ini",
        "cfg": "ini",
        # Note: CBOR (RFC 7049) is deprecated, use CBOR (RFC 8949) instead.
        "cbor": "cbor2",
        # https://github.com/msgpack/msgpack/issues/291#issuecomment-1370526984
        "mpk": "msgpack",
        "pkl": "pickle",
    }
    BINARY_DATA_PARSERS: ClassVar[set[str]] = {
        "ion",
        "bson",
        "cbor",
        "cbor2",
        "msgpack",
        "pickle",
    }
    SUPPORTED_PARSERS: list[str] = anyconfig.list_types()

    def __init__(
        self,
        resource: RawResourceT,
        parser_name: str | None = None,
        processor_class: type[Processor[ConfigModelT]] | None = None,
        *,
        create_if_missing: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Parameters
        ----------
        resource
            The URL to the configuration file, or a file-like object.
        parser_name
            The name of the anyconfig parser to use
            for loading and saving the configuration.
        create_if_missing
            Whether to automatically create missing keys when loading the configuration.
        default_kwargs
            Default keyword arguments to pass while opening the resource.
        use_pydantic_json
            Whether to use Pydantic's JSON encoder/decoder instead of the default
            anyconfig one.
        uses_binary_data
            Whether to treat the data as binary.
            Defaults to True for formats listed in `ConfigAgent.BINARY_DATA_PARSERS`.
        processor_class
            The processor class to use. Defaults to `configzen.Processor`.
        **kwargs
            Additional keyword arguments to pass to
            `anyconfig.loads()` and `anyconfig.dumps()`.
        """
        self._parser_name = None
        self._uses_binary_data = kwargs.get("uses_binary_data", False)

        if processor_class is None:
            processor_class = Processor[ConfigModelT]

        self.processor_class = processor_class
        self.parser_name = parser_name

        if isinstance(resource, (str, os.PathLike)) and not (
            isinstance(resource, str)
            and urllib.parse.urlparse(str(resource)).scheme in ALL_URL_SCHEMES
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
            "default_kwargs",
            self.predefined_default_kwargs.copy(),
        )
        self.allowed_url_schemes = kwargs.pop(
            "allowed_url_schemes",
            self.default_allowed_url_schemes.copy(),
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
        Get the resource of the configuration.

        This can be a file path, a URL, or a file-like object.

        .. note::
            If the resource is a file path, the processor will be guessed
            from the file extension.

        Returns
        -------
        The resource of the configuration.
        """
        self._resource = value
        if self.parser_name is None:
            self.parser_name = self._guess_parser_name()

    @property
    def parser_name(self) -> str | None:
        return self._parser_name

    @parser_name.setter
    def parser_name(self, value: str | None) -> None:
        if value is not None:
            value = value.casefold()
        self._parser_name = value

    def _guess_parser_name(self) -> str | None:
        parser_name = None
        if isinstance(self.resource, pathlib.Path):
            suffix = self.resource.suffix[1:].casefold()
            supported_parsers = self.SUPPORTED_PARSERS
            if not suffix:
                recognized_file_extensions = supported_parsers + [
                    alias + "(-> " + actual_parser_name + ")"
                    for alias, actual_parser_name in self.EXTRA_FILE_EXTENSIONS.items()
                    if actual_parser_name in supported_parsers
                ]
                msg = (
                    "Could not guess the anyconfig parser to use for "
                    f"{self.resource!r}.\n"
                    f"Recognized file extensions: {recognized_file_extensions}"
                )
                raise UnspecifiedParserError(msg)
            parser_name = self.EXTRA_FILE_EXTENSIONS.get(suffix, suffix)
            if (
                parser_name == "cbor2"
                and "cbor2" not in supported_parsers
                and "cbor" in supported_parsers
            ):
                parser_name = "cbor"
        return parser_name

    def load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str | bytes,
        parser_name: str | None = None,
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
        parser_name
            The name of the engines to use for loading the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration.
        """
        dict_config = self.load_dict(blob, parser_name=parser_name, **kwargs)
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    async def async_load_into(
        self,
        config_class: type[ConfigModelT],
        blob: str | bytes,
        parser_name: str | None = None,
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
        parser_name
            The name of the engines to use for loading the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration.
        """
        dict_config = await self.load_dict_async(
            blob,
            parser_name=parser_name,
            **kwargs,
        )
        if dict_config is None:
            dict_config = {}
        return config_class.parse_obj(dict_config)

    def _load_dict_impl(
        self,
        blob: str | bytes,
        parser_name: str | None = None,
        **kwargs: object,
    ) -> dict[str, Any]:
        parser_name = parser_name or self.parser_name or self._guess_parser_name()
        if parser_name is None:
            msg = "Cannot read configuration because `parser_name` was not specified"
            raise UnspecifiedParserError(msg)
        kwargs = {**self.load_options, **kwargs}
        try:
            loaded = anyconfig.loads(  # type: ignore[no-untyped-call]
                blob,
                ac_parser=parser_name,
                **kwargs,
            )
        except anyconfig.UnknownParserTypeError as exc:
            raise UnavailableParserError(str(exc).split()[-1], self) from exc
        if not isinstance(loaded, Mapping):
            msg = (
                f"Expected a mapping as a result of loading {self.resource}, "
                f"got {type(loaded).__name__}."
            )
            raise TypeError(msg)
        return dict(loaded)

    def load_dict(
        self,
        blob: str | bytes,
        parser_name: str | None = None,
        *,
        preprocess: bool = True,
        **kwargs: object,
    ) -> dict[str, Any]:
        """
        Load the configuration into a dictionary. The dictionary is
        usually used to initialize a `ConfigModel` subclass. If the
        configuration is empty, None might be returned instead of a dictionary.

        Parameters
        ----------
        blob
            The configuration to load.
        parser_name
            The name of the anyconfig parser to use for loading the configuration.
        preprocess
            Whether to preprocess the configuration (handle ^extend: directives etc.).
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration dictionary.
        """
        loaded = self._load_dict_impl(blob, parser_name=parser_name, **kwargs)
        if preprocess:
            loaded = self.processor_class(self, loaded).preprocess()
        return loaded

    async def load_dict_async(
        self,
        blob: str | bytes,
        parser_name: str | None = None,
        *,
        preprocess: bool = True,
        **kwargs: object,
    ) -> dict[str, Any]:
        """
        Load the configuration into a dictionary asynchronously.

        Parameters
        ----------
        blob
            The configuration to load.
        parser_name
            The name of the anyconfig parser to use for loading the configuration.
        preprocess
            Whether to preprocess the configuration (handle ^extend: directives etc.).
        **kwargs
            Additional keyword arguments to pass to `anyconfig.loads()`.

        Returns
        -------
        The loaded configuration dictionary.
        """
        loaded = self._load_dict_impl(blob, parser_name, **kwargs)
        if preprocess:
            loaded = await self.processor_class(self, loaded).preprocess_async()
        return loaded

    def dump_config(
        self,
        config: ConfigModelT,
        parser_name: str | None = None,
        **kwargs: object,
    ) -> str:
        """
        Dump the configuration to a string.

        Parameters
        ----------
        config
            The configuration to dump.
        parser_name
            The name of the anyconfig parser to use for saving the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.dumps()`.

        Returns
        -------
        The dumped configuration.
        """
        if parser_name is None:
            parser_name = self.parser_name
        export_kwargs = filter_options(self.EXPORT_KWARGS, kwargs)
        if parser_name == "json" and self.use_pydantic_json:
            export_kwargs.update(
                filter_options(
                    self.JSON_KWARGS,
                    {**self.dump_options, **kwargs},
                ),
            )
            _exporting.set(True)  # noqa: FBT003
            return detached_context_run(config.json, **export_kwargs)
        data = export_model(config, **export_kwargs)
        return self.dump_data(data, parser_name=parser_name, **kwargs)

    async def dump_config_async(
        self,
        config: ConfigModelT,
        parser_name: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Dump the configuration to a string.

        Parameters
        ----------
        config
            The configuration to dump.
        parser_name
            The name of the anyconfig parser to use for saving the configuration.
        **kwargs
            Additional keyword arguments to pass to `anyconfig.dumps()`.

        Returns
        -------
        The dumped configuration.
        """
        if parser_name is None:
            parser_name = self.parser_name
        export_kwargs = filter_options(self.EXPORT_KWARGS, kwargs)
        if parser_name == "json" and self.use_pydantic_json:
            export_kwargs.update(
                filter_options(
                    self.JSON_KWARGS,
                    {**self.dump_options, **kwargs},
                ),
            )
            _exporting.set(True)  # noqa: FBT003
            return await detached_context_await(config.json_async, **export_kwargs)
        data = await export_model_async(config, **export_kwargs)
        return self.dump_data(data, parser_name=parser_name, **kwargs)

    def dump_data(
        self,
        data: dict[str, Any],
        parser_name: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Dump data to a string.

        Parameters
        ----------
        data
            The data to dump.
        parser_name
            The name of the anyconfig parser to use for saving the configuration.
        kwargs
            Additional keyword arguments to pass to `anyconfig.dumps()`.

        Returns
        -------
        The dumped configuration.
        """
        if parser_name is None:
            parser_name = self.parser_name
        if parser_name is None:
            msg = (
                "Cannot write configuration because `parser_name` was not specified"
                f"for agent {self}"
            )
            raise UnspecifiedParserError(msg)
        kwargs = {**self.dump_options, **kwargs}
        return anyconfig.dumps(export_hook(data), ac_parser=parser_name, **kwargs)

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
        """Whether the resource uses bytes for storing data, not str."""
        return self._uses_binary_data or self.parser_name in self.BINARY_DATA_PARSERS

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
            return cast(
                ConfigIO,
                pathlib.Path(self.resource).open(**kwds),  # noqa: SIM115, RUF100
            )
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
        if aiofiles is None:
            msg = (
                "Aiofiles is not available, cannot open file "
                "asynchronously (install with `pip install aiofiles`)"
            )
            raise RuntimeError(msg)
        if isinstance(self.resource, (int, pathlib.Path)):
            kwds = filter_options(self.OPEN_KWARGS, kwds)
            return aiofiles.open(self.resource, **kwds)
        msg = "Cannot open resource asynchronously"
        raise RuntimeError(msg)

    def processor_open_resource(self, **kwargs: object) -> ConfigIO:
        """
        Open a configuration resource, while preprocessing,
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

    def processor_open_resource_async(self, **kwargs: object) -> AsyncConfigIO:
        """
        Open a configuration resource asynchronously, while preprocessing,
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
        new_kwargs = cast("dict[str, Any]", kwargs).copy()
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
            else:
                raise
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
            # TODO(bswck): type-annotate it properly.
            # https://github.com/bswck/configzen/issues/11
            return await fp.write(cast(str, blob))

    @classmethod
    def from_directive_context(
        cls,
        ctx: DirectiveContext,
        /,
        route_separator: str = ":",
        route_class: type[ConfigRoute] | None = None,
    ) -> tuple[ConfigAgent[ConfigModelT], ConfigRouteLike | None]:
        """
        Create a configuration agent from a preprocessor directive context.
        Return an optional scope that the context points to.

        Parameters
        ----------
        route_class
            The class to use for the route.
        route_separator
            The separator to use for the route.
        ctx
            The directive context.

        Returns
        -------
        The configuration agent.
        """
        if route_class is None:
            route_class = ConfigRoute
        route: ConfigRouteLike | None = None
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        if isinstance(ctx.snippet, str):
            path, _, route = ctx.snippet.partition(route_separator)
            route = ConfigRoute(
                route.strip().replace(route_separator, route_class.TOK_DOT),
            )
            args.append(path)
        elif isinstance(ctx.snippet, int):
            args.append(ctx.snippet)
        elif is_dict_like(ctx.snippet):
            kwargs.update(ctx.snippet)
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
        parser_name: str,
    ) -> None:
        """
        Register a file extension with the proper anyconfig parser to use.

        Parameters
        ----------
        file_extension
            The file extension to register.
        parser_name
            The name of the anyconfig parser to use for loading the configuration.

        """
        cls.EXTRA_FILE_EXTENSIONS[file_extension] = parser_name

    def __repr__(self) -> str:
        resource = self.resource
        return f"{type(self).__name__}({resource=!r})"


def at(
    mapping: Any,
    route: ConfigRouteLike,
    converter_func: Callable[[Any], dict[str, Any]] = _get_object_state,
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
        The function to use for converting an object to a dictionary.
    agent
        The configuration agent.

    Returns
    -------
    The item at the route.
    """
    route = ConfigRoute(route)
    route_here = []
    scope = converter_func(mapping)
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
    route: ConfigRouteLike

    def get(
        self,
        route: ConfigRouteLike | None = None,
        default: Any = Undefined,
    ) -> Any:
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
        return await _partial_save_async(self, **kwargs)

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
        return _partial_save(self, **kwargs)

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
        return await _partial_reload_async(self, **kwargs)

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
        return _partial_reload(self, **kwargs)


def _partial_save(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    write_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> int:
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


async def _partial_save_async(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    write_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> int:
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


def _partial_reload(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    **kwargs: Any,
) -> Any:
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


async def _partial_reload_async(
    section: ConfigModelT | ConfigAt[ConfigModelT],
    **kwargs: Any,
) -> Any:
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
    interpolation_namespace: dict[str, Any]

    @abc.abstractmethod
    def trace_route(self) -> Iterator[str]:
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
        self,
        part: str | None,
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
        return Subcontext(self, part, self.interpolation_namespace.setdefault(part, {}))

    @property
    @abc.abstractmethod
    def agent(self) -> ConfigAgent[ConfigModelT]:
        """The configuration agent responsible for loading and saving."""

    @property
    @abc.abstractmethod
    def toplevel_interpolation_namespace(self) -> dict[str, Any]:
        """Top-level interpolation namespace."""

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
        self._initial_state = {}
        self._owner = None
        self._agent = agent
        self.interpolation_namespace = {}
        self.owner = owner

    def trace_route(self) -> Iterator[str]:
        yield from ()

    @property
    def agent(self) -> ConfigAgent[ConfigModelT]:
        return self._agent

    @property
    def toplevel_interpolation_namespace(self) -> dict[str, Any]:
        return self.interpolation_namespace

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

    __slots__ = ("_parent", "_part", "_interpolation_namespace")

    def __init__(
        self,
        parent: BaseContext[ConfigModelT],
        part: str,
        interpolation_namespace: dict[str, Any],
    ) -> None:
        self._parent = parent
        self._part = part
        self.interpolation_namespace = interpolation_namespace

    @property
    def agent(self) -> ConfigAgent[ConfigModelT]:
        return self._parent.agent

    @property
    def toplevel_interpolation_namespace(self) -> dict[str, Any]:
        return self._parent.toplevel_interpolation_namespace

    def trace_route(self) -> Iterator[str]:
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
        msg = (
            "This model is either inside a list or was not loaded "
            "by a configuration agent."
        )
        raise RuntimeError(msg)
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
        Optional[BaseContext[ConfigModelT]],
        getattr(config, LOCAL).get(current_context),
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
        try:
            interpolated = interpolate(
                post_hook_value,
                cls,
                values.copy(),
                field.outer_type_,
            )
        except InterpolationError as err:
            err.message += f" (encountered in {cls.__qualname__}.{field.alias})"
            raise

        new_value = field_hook(field.outer_type_, interpolated)
        if old_value != new_value:
            interpolation_tracker[field.alias] = (old_value, copy.copy(new_value))
        post_hook_value = new_value

    return post_hook_value


def _json_encoder(model_encoder: Callable[..., Any], value: Any, **kwargs: Any) -> Any:
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
        **kwargs: object,
    ) -> type:
        namespace.update(
            {
                **dict.fromkeys(
                    (EXPORT, CONTEXT, LOCAL, TOKEN, MODULE),
                    pydantic.PrivateAttr(),
                ),
                INTERPOLATION_TRACKER: pydantic.PrivateAttr(default_factory=dict),
            },
        )

        if namespace.get(INTERPOLATION_INCLUSIONS) is None:
            namespace[INTERPOLATION_INCLUSIONS] = {}

        if namespace.get(INTERPOLATOR) is None:
            namespace[INTERPOLATOR] = BaseInterpolator()

        if namespace.get(EVALUATION_ENGINE) is None:
            namespace[EVALUATION_ENGINE] = BaseEvaluationEngine()

        if kwargs.pop("root", None):
            return type.__new__(cls, name, bases, namespace, **kwargs)

        model = super().__new__(cls, name, bases, namespace, **kwargs)
        for field in model.__fields__.values():
            if field.pre_validators is None:
                field.pre_validators = []
            field.pre_validators[:] = [_common_field_validator, *field.pre_validators]
            if type(field.outer_type_) is ConfigModelMetaclass:
                validator = make_generic_validator(
                    field.outer_type_.__field_setup__,  # type: ignore[attr-defined]
                )
                field.pre_validators[:] = [
                    _common_field_validator,
                    validator,
                    *field.pre_validators,
                ]
        model_encoder = model.__json_encoder__
        model.__json_encoder__ = functools.partial(_json_encoder, model_encoder)
        return cast(type, model)


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

    parser_name
        The anyconfig parser to use.

    autoupdate_forward_refs
        Whether to automatically update forward references
        when `ConfigModel.load()` or `ConfigModel.load_async()`
        methods are called. For convenience, defaults to `True`.

    And all other attributes from `pydantic.BaseSettings.Config`.
    """

    resource: ConfigAgent[ConfigModel] | RawResourceT | None = None
    parser_name: str | None = None
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

    module_wrapper_class: ClassVar[type[ConfigModule[ConfigModel]]] = ConfigModule

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
        self: ConfigModelT,
        memodict: dict[Any, Any] | None = None,
    ) -> ConfigModelT:
        state = self.__dict__.copy()
        state.pop(LOCAL, None)
        state.pop(TOKEN, None)
        clone = copy.deepcopy(state)
        return type(self).parse_obj(
            {
                field.alias: clone[field_name]
                for field_name, field in self.__fields__.items()
            },
        )

    def __setattr__(self, key: str, value: object) -> None:
        getattr(self, LOCAL).run(super().__setattr__, key, value)

    def _init_private_attributes(self) -> None:
        super()._init_private_attributes()
        local = contextvars.copy_context()
        object.__setattr__(self, LOCAL, local)
        tok = getattr(self, TOKEN, None)
        if tok:
            context = current_context.get()
            if context is not None:
                context.interpolation_namespace.update(self.dict())
            current_context.reset(tok)

    def export(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        _exporting.set(True)  # noqa: FBT003
        return detached_context_run(self.dict, **kwargs)

    async def export_async(self, **kwargs: Any) -> dict[str, Any]:
        """
        Export the configuration model.

        Returns
        -------
        The exported configuration model.
        """
        _exporting.set(True)  # noqa: FBT003
        return await detached_context_await(self.dict_async, **kwargs)

    async def dict_async(self, **kwargs: Any) -> dict[str, Any]:
        """
        Get the dictionary representation of the configuration model.

        Returns
        -------
        The dictionary representation of the configuration model.
        """
        return dict(await self._iter_async(to_dict=True, **kwargs))

    # noinspection PyShadowingNames
    async def json_async(
        self,
        include: IncludeExcludeT = None,
        exclude: IncludeExcludeT = None,
        *,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        encoder: Callable[[Any], Any] | None = None,
        models_as_dict: bool = True,
        **dumps_kwargs: Any,
    ) -> str:
        encoder = cast(Callable[[Any], Any], encoder or self.__json_encoder__)
        data = dict(
            await self._iter_async(
                to_dict=models_as_dict,
                by_alias=by_alias,
                include=include,
                exclude=exclude,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            ),
        )
        if self.__custom_root_type__:
            data = data[ROOT_KEY]
        return self.__config__.json_dumps(data, default=encoder, **dumps_kwargs)

    def _iter(  # type: ignore[override]
        self,
        **kwargs: Any,
    ) -> Iterator[tuple[str, Any]]:
        if kwargs.get("to_dict", False) and _exporting.get():
            state: dict[str, Any] = {}
            for key, value in super()._iter(**kwargs):
                state.update([self._export_iter_hook(key, value)])
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                context.agent.processor_class.export(state, metadata=metadata)
            yield from state.items()
        else:
            yield from super()._iter(**kwargs)

    async def _iter_async(
        self,
        **kwargs: Any,
    ) -> Iterator[tuple[str, Any]]:
        if kwargs.get("to_dict", False) and _exporting.get():
            state: dict[str, Any] = {}
            for key, value in super()._iter(**kwargs):
                state.update([self._export_iter_hook(key, value)])
            metadata = getattr(self, EXPORT, None)
            if metadata:
                context = get_context(self)
                await context.agent.processor_class.export_async(
                    state,
                    metadata=metadata,
                )
            return ((key, value) for key, value in state.items())
        return super()._iter(**kwargs)

    def _export_iter_hook(
        self,
        key: str,
        value: Any,
    ) -> tuple[str, Any]:
        interpolation_tracker = getattr(self, LOCAL).get(current_interpolation_tracker)
        field = self.__fields__.get(key)
        actual_key = field.alias if field else key
        if interpolation_tracker:
            interpolation_track = interpolation_tracker.get(actual_key)
            if interpolation_track:
                old_value, new_value = interpolation_track

                # if value != new_value:
                #     InterpolationError:
                #         Cannot restore the value of {actual_key!r}
                #         before interpolation

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
        parser_name: str | None = None,
    ) -> ConfigAgent[ConfigModelT]:
        if resource is None:
            resource = getattr(cls.__config__, "resource", None)
        if resource is None:
            msg = "No resource specified"
            raise ValueError(msg)
        if parser_name is None:
            parser_name = getattr(cls.__config__, "parser_name", None)
        agent: ConfigAgent[ConfigModelT]
        if isinstance(resource, ConfigAgent):
            agent = resource
        else:
            agent = ConfigAgent(
                resource,
                parser_name=parser_name,
            )
        if create_if_missing is not None:
            agent.create_if_missing = create_if_missing
        if parser_name is not None:
            agent.parser_name = cast(str, parser_name)
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
        route: ConfigRouteLike | None = None,
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
    def get(self, route: ConfigRouteLike = ..., default: Any = ...) -> Any:
        ...

    def get(
        self,
        route: ConfigRouteLike | None = None,
        default: Any = Undefined,
    ) -> Any:
        """
        Get a value from the configuration.

        Parameters
        ----------
        route
            Route to access the item. If None, the whole configuration is returned.
        default
            The default value to return if the item is not found.
            If not specified, an exception is raised (ConfigAccessError).
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
    def load(
        cls: type[ConfigModelT],
        resource: ConfigAgent[ConfigModelT] | RawResourceT | None = None,
        *,
        create_if_missing: bool | None = None,
        parser_name: str | None = None,
        **kwargs: Any,
    ) -> ConfigModelT:
        """
        Load the configuration file.

        To reload the configuration, use the `reload()` method.

        Parameters
        ----------
        resource
            The configuration resource to read from/write to.
        parser_name
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
            parser_name=parser_name,
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
        context = cast("Context[ConfigModelT]", local.get(current_context))
        context.owner = config
        context.initial_state = config.__dict__
        return config

    @classmethod
    async def load_async(
        cls: type[ConfigModelT],
        resource: ConfigAgent[ConfigModelT] | RawResourceT | None = None,
        *,
        parser_name: str | None = None,
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
        parser_name
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
            create_if_missing=create_if_missing,
            parser_name=parser_name,
        )
        current_context.set(Context(agent))
        local = contextvars.copy_context()
        if getattr(
            cls.__config__,
            "autoupdate_forward_refs",
            ConfigMeta.autoupdate_forward_refs,
        ):
            cls.update_forward_refs()
        reader = local.run(
            asyncio.create_task,
            agent.read_async(config_class=cls, **kwargs),
        )
        config = await reader
        object.__setattr__(config, LOCAL, local)
        context = cast("Context[ConfigModelT]", local.get(current_context))
        context.owner = config
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
        try:
            context = get_context(self)
        except RuntimeError:
            wrapped_module = getattr(self, MODULE, None)
            if wrapped_module is None:
                raise
            importlib.reload(wrapped_module)
            self.update(
                {
                    key: value
                    for key, value in vars(wrapped_module).items()
                    if key in {field.alias for field in self.__fields__.values()}
                },
            )
            return self
        current_context.set(get_context(context.owner))
        if context.owner is self:
            changed = context.agent.read(config_class=type(self), **kwargs)
        else:
            changed = _partial_reload(cast(ConfigModelT, context.at), **kwargs)
        state = changed.__dict__
        context.initial_state = state
        self.update(state)
        return self

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
            changed = await _partial_reload_async(
                cast("ConfigAt[ConfigModelT]", context.at),
                **kwargs,
            )
        state = changed.__dict__
        context.initial_state = state
        self.update(state)
        return self

    def save(
        self: ConfigModelT,
        write_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
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
        return _partial_save(
            cast("ConfigAt[ConfigModelT]", context.at),
            write_kwargs=write_kwargs,
            **kwargs,
        )

    async def save_async(
        self: ConfigModelT,
        write_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
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
        return await _partial_save_async(
            cast("ConfigAt[ConfigModelT]", context.at),
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

    async def write_async(self, blob: str | bytes, **kwargs: Any) -> int:
        """
        Overwrite the configuration file asynchronously
        with the given string or bytes.

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

    @classmethod
    def _evaluate_interpolation_namespaces(cls) -> dict[str | None, dict[str, Any]]:
        inclusions = getattr(cls, INTERPOLATION_INCLUSIONS)
        return {
            namespace_id: evaluate_namespace()
            for namespace_id, evaluate_namespace in inclusions.items()
        }

    @classmethod
    def _evaluate_interpolation_expression(
        cls,
        expression: str,
        *,
        result_namespace: dict[str, Any],
        namespaces: dict[str | None, dict[str, Any]],
        closest_namespace: dict[str, Any],
        target_type: type[Any],
    ) -> Any:
        evaluation_engine: BaseEvaluationEngine = getattr(cls, EVALUATION_ENGINE)
        return evaluation_engine.evaluate_expression(
            expression=expression,
            result_namespace=result_namespace,
            namespaces=namespaces,
            closest_namespace=closest_namespace,
            target_type=target_type,
        )

    @classmethod
    def wrap_module(
        cls: type[ConfigModelT],
        module_name: str | types.ModuleType,
        package: str | None = None,
        /,
        **values: Any,
    ) -> ConfigModelT:
        module_vars = None
        if isinstance(module_name, str):
            if module_name not in sys.modules:
                if package is None and module_name.startswith("."):
                    current_frame = inspect.currentframe()
                    assert current_frame is not None
                    frame_back = current_frame.f_back
                    assert frame_back is not None
                    package = frame_back.f_globals["__package__"]
                module_vars = vars(
                    importlib.import_module(module_name, package=package),
                )
        else:
            module_name = module_name.__name__
        config_module = cls.module_wrapper_class.wrap_module(
            module_name,
            cls,
            module_vars,
            **values,
        )
        return cast(ConfigModelT, config_module.get_model())

    @classmethod
    def wrap_this_module(
        cls: type[ConfigModelT],
        **values: Any,
    ) -> ConfigModelT:
        current_frame = inspect.currentframe()
        assert current_frame is not None
        frame_back = current_frame.f_back
        assert frame_back is not None
        return cls.wrap_module(frame_back.f_globals["__name__"], **values)

    @classmethod
    def get_interpolation_namespace(
        cls,
        expressions: set[str],
        closest_namespace: dict[str, Any],
        target_type: type[Any],
    ) -> dict[str, Any]:
        """Get the interpolation namespace according to occuring expressions."""
        context = current_context.get()
        result_namespace: dict[str, Any] = {}

        namespaces = cls._evaluate_interpolation_namespaces()
        if context is not None:
            namespaces.setdefault(None, {}).update(
                context.toplevel_interpolation_namespace,
            )

        for expression in expressions:
            value = cls._evaluate_interpolation_expression(
                expression=expression,
                result_namespace=result_namespace,
                namespaces=namespaces,
                closest_namespace=closest_namespace,
                target_type=target_type,
            )
            result_namespace[expression] = value

        return result_namespace

    @classmethod
    def __field_setup__(
        cls,
        value: dict[str, Any],
        field: ModelField,
    ) -> Any:
        """
        Set up this configuration model as it is being initialized as a field
        of some other configuration model.
        """
        context = current_context.get()
        if context is not None:
            subcontext = context.enter(field.name)
            tok = current_context.set(subcontext)
            return {
                **_get_object_state(value),
                TOKEN: tok,
                LOCAL: contextvars.copy_context(),
            }
        return value

    if not TYPE_CHECKING:
        load = detached_context_function(load)
        load_async = detached_context_function(load_async)
        reload = detached_context_function(reload)
        reload_async = detached_context_function(reload_async)
        save = detached_context_function(save)
        save_async = detached_context_function(save_async)
        export = detached_context_function(export)
        export_async = detached_context_function(export_async)


setattr(ConfigModel, INTERPOLATION_INCLUSIONS, None)
include.register(ConfigModel, include_const)

if os.getenv("CONFIGZEN_SETUP") != "0":
    importlib.import_module("._setup", package=__package__)
