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
    overload,
)

from anyio import Path as AsyncPath
from runtime_generics import generic_isinstance, runtime_generic

if TYPE_CHECKING:
    from collections.abc import Callable

    from typing_extensions import Unpack

    from configzen.data import Data, DataFormat

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
    "ConfigurationSource",
    "FileConfigurationSource",
    "get_configuration_source",
    "get_configuration_source_file",
)

SourceType = TypeVar("SourceType")


class ConfigurationSource(Generic[SourceType, AnyStr], metaclass=ABCMeta):
    """
    Core interface for loading and saving configuration data.

    If you need to implement your own configuration source class,
    implement a subclass of this class and pass in to the `.configuration_load()` method
    of your configuration or its model_config.
    """

    data_format: DataFormat[Any, AnyStr]

    def __init__(
        self,
        source: SourceType,
        data_format: DataFormat[Any, AnyStr] | None = None,
        **options: Unpack[FormatOptions],
    ) -> None:
        self.source = source
        self.options = options
        if data_format is None:
            actual_data_format = self._guess_data_format()
        else:
            actual_data_format = data_format
        actual_data_format.validate_source(self)
        self.data_format = actual_data_format

    def _guess_data_format(self) -> DataFormat[Any, AnyStr]:
        msg = "Cannot guess the data format of the configuration source"
        raise NotImplementedError(msg)

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
    def dump(self, data: Data) -> int:
        """
        Dump the configuration source.

        Return the number of bytes written.
        """
        raise NotImplementedError

    @abstractmethod
    async def dump_async(self, data: Data) -> int:
        """
        Dump the configuration source asynchronously.

        Return the number of bytes written.
        """
        raise NotImplementedError


@singledispatch
def get_configuration_source(
    source: object,
) -> ConfigurationSource[Any, Any]:
    """Get a dedicated interface for a configuration source."""
    type_name = type(source).__name__
    msg = (
        f"There is no class operating on {type_name!r} configuration "
        f"sources. Implement it by creating a subclass of ConfigurationSource."
    )
    raise NotImplementedError(msg)


@runtime_generic
class FileConfigurationSource(
    ConfigurationSource[Path, AnyStr],
    Generic[AnyStr],
):
    """
    A configuration source that is a file.

    Parameters
    ----------
    source
        The path to the configuration source file.
    """

    def __init__(self, source: str | bytes | PathLike[str] | PathLike[bytes]) -> None:
        super().__init__(self._make_path(source))
        self._stream_class: Callable[..., IO[AnyStr]] = (
            BytesIO if self._is_binary() else StringIO
        )

    def _guess_data_format(self) -> DataFormat[Any, AnyStr]:
        suffix = self.source.suffix
        if suffix:
            extension = suffix[1:]
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

    def _make_path(
        self,
        source: str | bytes | PathLike[str] | PathLike[bytes],
    ) -> Path:
        if isinstance(source, PathLike):
            source = source.__fspath__()
        if isinstance(source, bytes):
            source = source.decode()
        return Path(source)

    # This is not a property for type safety reasons.
    # https://github.com/python/mypy/issues/9937
    @overload
    def _is_binary(self: FileConfigurationSource[str]) -> Literal[False]:
        ...

    @overload
    def _is_binary(self: FileConfigurationSource[bytes]) -> Literal[True]:
        ...

    def _is_binary(self: FileConfigurationSource[AnyStr]) -> bool:
        return generic_isinstance(self, FileConfigurationSource[bytes])

    def load(self) -> Data:
        """
        Load the configuration source file and return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(self._stream_class(self.read()))

    async def load_async(self) -> Data:
        """
        Load the configuration source file asynchronously.

        Return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(self._stream_class(await self.read_async()))

    def dump(self, data: Data) -> int:
        """
        Load the configuration source file asynchronously.

        Return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = self._stream_class()
        self.data_format.dump(data, stream)
        return self.write(stream.read())

    async def dump_async(self, data: Data) -> int:
        """
        Load the configuration source file asynchronously.

        Return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = self._stream_class()
        self.data_format.dump(data, stream)
        return await self.write_async(stream.read())

    def read(self) -> AnyStr:
        """Read the configuration source and return its contents."""
        if self._is_binary():
            return self.source.read_bytes()
        return self.source.read_text()

    async def read_async(self) -> AnyStr:
        """Read the configuration source file asynchronously and return its contents."""
        if self._is_binary():
            return await AsyncPath(self.source).read_bytes()
        return await AsyncPath(self.source).read_text()

    def write(self, content: AnyStr) -> int:
        """
        Write the configuration source file and return the number of bytes written.

        Parameters
        ----------
        content
            The content to write to the configuration source.
        """
        if self._is_binary():
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
        if self._is_binary():
            return await AsyncPath(self.source).write_bytes(content)
        return await AsyncPath(self.source).write_text(content)


@get_configuration_source.register(str)
@get_configuration_source.register(bytes)
@get_configuration_source.register(PathLike)
def get_configuration_source_file(
    source: str | bytes | PathLike[str] | PathLike[bytes],
) -> FileConfigurationSource[str] | FileConfigurationSource[bytes]:
    """Get a dedicated interface for a configuration source file."""
    return FileConfigurationSource(source)
