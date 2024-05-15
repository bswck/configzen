"""Sources and destinations that hold the configuration data."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from functools import singledispatch
from io import BytesIO, StringIO
from os import PathLike
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    AnyStr,
    Generic,
    Literal,
    TypedDict,
    TypeVar,
)

from anyio import Path as AsyncPath
from runtime_generics import runtime_generic, type_check

from configzen.data import DataFormat

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import ClassVar, overload

    from typing_extensions import Never, Unpack

    from configzen.data import Data

    # We actually depend on `option_name` of every data format at runtime,
    # but this time we pretend it doesn't exist.
    from configzen.formats.std_json import JSONOptions
    from configzen.formats.std_plist import PlistOptions
    from configzen.formats.toml import TOMLOptions
    from configzen.formats.yaml import YAMLOptions

    class FormatOptions(TypedDict, total=False):
        json: JSONOptions
        plist: PlistOptions
        toml: TOMLOptions
        yaml: YAMLOptions


__all__ = (
    "ConfigSource",
    "FileConfigSource",
    "StreamConfigSource",
    "get_config_source",
    "get_stream_config_source",
    "get_file_config_source",
)

SourceType = TypeVar("SourceType")


@runtime_generic
class ConfigSource(Generic[SourceType, AnyStr], metaclass=ABCMeta):
    """
    Core interface for loading and saving configuration data.

    If you need to implement your own configuration source class,
    implement a subclass of this class and pass in to the `.config_load()` method
    of your configuration or its model_config.
    """

    # Set up temporary stream factories
    _binary_stream_factory: ClassVar[Callable[..., IO[bytes]]] = BytesIO
    _string_stream_factory: ClassVar[Callable[..., IO[str]]] = StringIO
    _data_format: DataFormat[Any, AnyStr]
    source: SourceType
    options: FormatOptions

    def __init__(
        self,
        source: SourceType,
        data_format: str | DataFormat[Any, AnyStr] | None = None,
        **options: Unpack[FormatOptions],
    ) -> None:
        self._temp_stream_factory: Callable[..., IO[AnyStr]] = (
            self._binary_stream_factory
            if self.is_binary()
            else self._string_stream_factory
        )
        self.source = source
        self.options = options
        self.data_format = data_format  # type: ignore[assignment]

    @property
    def data_format(self) -> DataFormat[Any, AnyStr]:
        """The current data format for a configuration source."""
        return self._data_format

    @data_format.setter
    def data_format(self, data_format: str | DataFormat[Any, AnyStr] | None) -> None:
        if data_format is None:
            data_format = self._guess_data_format()
        else:
            data_format = self._make_data_format(data_format)
        data_format.validate_source(self)
        self._data_format = data_format

    def _guess_data_format(self) -> DataFormat[Any, AnyStr]:
        msg = "Cannot guess the data format of the configuration source"
        raise NotImplementedError(msg)

    def _make_data_format(
        self,
        data_format: str | DataFormat[Any, AnyStr],
    ) -> DataFormat[Any, AnyStr]:
        if isinstance(data_format, str):
            return DataFormat.for_extension(
                data_format,
                self.options.get(data_format),  # type: ignore[arg-type]
            )
        data_format.configure(**self.options)  # type: ignore[misc]
        return data_format

    if TYPE_CHECKING:
        # python/mypy#9937
        @overload
        def is_binary(self: ConfigSource[SourceType, str]) -> Literal[False]: ...

        @overload
        def is_binary(
            self: ConfigSource[SourceType, bytes],
        ) -> Literal[True]: ...

    def is_binary(self: ConfigSource[SourceType, AnyStr]) -> bool:
        """Determine whether the configuration source is binary."""
        return not type_check(self, ConfigSource[Any, str])

    @abstractmethod
    def load(self) -> Data:
        """
        Load the configuration source.

        Return its contents as a dictionary.
        """
        raise NotImplementedError

    @abstractmethod
    async def load_async(self) -> Data:
        """
        Load the configuration source asynchronously.

        Return its contents as a dictionary.
        """
        raise NotImplementedError

    @abstractmethod
    def dump(self, data: Data) -> None:
        """Dump the configuration source."""
        raise NotImplementedError

    @abstractmethod
    async def dump_async(self, data: Data) -> int:
        """Dump the configuration source asynchronously."""
        raise NotImplementedError


@singledispatch
def get_config_source(
    source: object,
    _data_format: DataFormat[Any, AnyStr] | None = None,
) -> ConfigSource[Any, Any]:
    """Get a dedicated interface for a configuration source."""
    type_name = type(source).__name__
    msg = (
        f"There is no class operating on {type_name!r} configuration "
        f"sources. Implement it by creating a subclass of ConfigSource."
    )
    raise NotImplementedError(msg)


def _make_path(
    source: str | bytes | PathLike[str] | PathLike[bytes],
) -> Path:
    if isinstance(source, PathLike):
        source = source.__fspath__()
    if isinstance(source, bytes):
        source = source.decode()
    return Path(source)


@runtime_generic
class StreamConfigSource(
    Generic[AnyStr],
    ConfigSource[IO[Any], Any],
):
    """
    A configuration source that is a stream.

    Parameters
    ----------
    source
        The stream to the configuration source.

    """

    def __init__(
        self,
        source: IO[AnyStr],
        data_format: str | DataFormat[Any, AnyStr],
        **options: Unpack[FormatOptions],
    ) -> None:
        super().__init__(source, data_format=data_format, **options)

    def load(self) -> Data:
        """
        Load the configuration source.

        Return its contents as a dictionary.
        """
        return self.data_format.load(self.source)

    def load_async(self) -> Never:
        """Unsupported."""
        msg = "async streams are not supported for `StreamConfigSource`"
        raise NotImplementedError(msg)

    def dump(self, data: Data) -> None:
        """Dump the configuration source."""
        self.data_format.dump(data, self.source)

    def dump_async(self, _data: Data) -> Never:
        """Unsupported."""
        msg = "async streams are not supported for `StreamConfigSource`"
        raise NotImplementedError(msg)


@get_config_source.register(BytesIO)
@get_config_source.register(StringIO)
def get_stream_config_source(
    source: IO[bytes] | IO[str],
    data_format: DataFormat[Any, Any],
) -> StreamConfigSource[str] | StreamConfigSource[bytes]:
    """Get a dedicated interface for a configuration source stream."""
    return StreamConfigSource(source, data_format=data_format)


@runtime_generic
class FileConfigSource(
    Generic[AnyStr],
    ConfigSource[Path, AnyStr],
):
    """
    A configuration source that is a file.

    Parameters
    ----------
    source
        The path to the configuration source file.

    """

    def __init__(
        self,
        source: str | bytes | PathLike[str] | PathLike[bytes],
        data_format: str | DataFormat[Any, Any] | None = None,
        *,
        use_processing_trace: bool = True,
        **options: Unpack[FormatOptions],
    ) -> None:
        super().__init__(_make_path(source), data_format=data_format, **options)
        self._use_processing_trace = use_processing_trace

    @property
    def paths(self) -> list[Path]:
        """List possible path variants basing on the processing context trace."""
        from configzen.config import processing

        if (
            not self.source.is_absolute()
            and self._use_processing_trace
            and (processing_context := processing.get())
        ):
            return [
                _make_path(source).parent / self.source
                for config_source in processing_context.trace
                if isinstance(source := config_source.source, (str, bytes, PathLike))
            ]
        return [self.source]  # in current working dir

    def _guess_data_format(self) -> DataFormat[Any, AnyStr]:
        suffix = self.source.suffix
        if suffix:
            extension = suffix.replace(".", "", 1)
            from configzen.data import DataFormat

            data_format_class = DataFormat.extension_registry.get(extension)
            if data_format_class is not None:
                return data_format_class(
                    self.options.get(data_format_class.option_name) or {},
                )
        msg = (
            f"Cannot guess the data format of the configuration source "
            f"with extension {suffix!r}"
        )
        raise NotImplementedError(msg)

    def _after_load(self) -> None:
        from configzen.config import processing

        processing_context = processing.get()
        if processing_context:
            processing_context.trace.append(self)

    def load(self) -> Data:
        """
        Load the configuration source file.

        Return its contents as a dictionary.
        """
        data = self.data_format.load(self._temp_stream_factory(self.read()))
        self._after_load()
        return data

    async def load_async(self) -> Data:
        """
        Load the configuration source file asynchronously.

        Return its contents as a dictionary.
        """
        data = self.data_format.load(self._temp_stream_factory(await self.read_async()))
        self._after_load()
        return data

    def dump(self, data: Data) -> None:
        """
        Dump the configuration data to the source file.

        Parameters
        ----------
        data
            The data to dump to the configuration source.

        """
        temp_stream = self._temp_stream_factory()
        self.data_format.dump(data, temp_stream)
        temp_stream.seek(0)
        self.write(temp_stream.read())

    async def dump_async(self, data: Data) -> int:
        """
        Load the configuration source file asynchronously.

        Return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.

        """
        temp_stream = self._temp_stream_factory()
        self.data_format.dump(data, temp_stream)
        temp_stream.seek(0)
        return await self.write_async(temp_stream.read())

    def read(self) -> AnyStr:
        """Read the configuration source and return its contents."""
        errors = []
        reader = Path.read_bytes if self.is_binary() else Path.read_text
        for path in self.paths:
            try:
                return reader(path)
            except FileNotFoundError as e:  # noqa: PERF203
                errors.append(e)
                continue
        raise FileNotFoundError(errors)

    async def read_async(self) -> AnyStr:
        """Read the configuration source file asynchronously and return its contents."""
        errors = []
        reader = AsyncPath.read_bytes if self.is_binary() else AsyncPath.read_text
        for path in map(AsyncPath, self.paths):
            try:
                return await reader(path)
            except FileNotFoundError as e:  # noqa: PERF203
                errors.append(e)
                continue
        raise FileNotFoundError(errors)

    def write(self, content: AnyStr) -> int:
        """
        Write the configuration source file and return the number of bytes written.

        Parameters
        ----------
        content
            The content to write to the configuration source.

        """
        if self.is_binary():
            return self.source.write_bytes(content)
        return self.source.write_text(content)

    async def write_async(self, content: AnyStr) -> int:
        """
        Write the configuration source file asynchronously.

        Return the number of bytes written.

        Parameters
        ----------
        content
            The content to write to the configuration source.

        """
        if self.is_binary():
            return await AsyncPath(self.source).write_bytes(content)
        return await AsyncPath(self.source).write_text(content)


@get_config_source.register(str)
@get_config_source.register(bytes)
@get_config_source.register(PathLike)
def get_file_config_source(
    source: str | bytes | PathLike[str] | PathLike[bytes],
    data_format: DataFormat[Any, AnyStr] | None = None,
) -> FileConfigSource[str] | FileConfigSource[bytes]:
    """Get a dedicated interface for a configuration source file."""
    return FileConfigSource(source, data_format=data_format)
