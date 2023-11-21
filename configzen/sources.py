from __future__ import annotations

from abc import ABCMeta, abstractmethod
from functools import singledispatch
from io import BytesIO, StringIO
from os import PathLike
from pathlib import Path
from typing import (
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

if TYPE_CHECKING:
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
    "BinaryFileConfigurationSource",
    "TextFileConfigurationSource",
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
        raise NotImplementedError

    @abstractmethod
    async def load_async(self) -> Data:
        raise NotImplementedError

    @abstractmethod
    def dump(self, data: Data) -> int:
        raise NotImplementedError

    @abstractmethod
    async def dump_async(self, data: Data) -> int:
        raise NotImplementedError


@singledispatch
def get_configuration_source(
    source: object,
    *,
    format_type: Literal["binary", "text"],
) -> ConfigurationSource[Any, Any]:
    """Get a dedicated interface for a configuration source."""
    type_name = type(source).__name__
    msg = (
        f"There is no class operating on {type_name!r} configuration "
        f"{format_type} sources. Implement it by creating "
        "a subclass of ConfigurationSource."
    )
    raise NotImplementedError(msg)


class FileConfigurationSource(
    ConfigurationSource[Path, AnyStr],
    Generic[AnyStr],
):
    def __init__(self, source: str | bytes | PathLike[str] | PathLike[bytes]) -> None:
        super().__init__(self._make_path(source))

    def _guess_data_format(self) -> DataFormat[Any, AnyStr]:
        suffix = self.source.suffix
        if suffix:
            extension = suffix[1:]
            from configzen.data import DataFormat

            data_format_class = DataFormat.extension_registry.get(extension)
            if data_format_class is not None:
                data_format = data_format_class(
                    self.options.get(data_format_class.option_name) or {},
                )
                return data_format
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

    @abstractmethod
    def read(self) -> AnyStr:
        """Read the configuration source and return its contents."""

    @abstractmethod
    async def read_async(self) -> AnyStr:
        """Read the configuration source asynchronously and return its contents."""

    @abstractmethod
    def write(self, content: AnyStr) -> int:
        """
        Write the configuration source file and return the number of bytes written.

        Parameters
        ----------
        content
            The content to write to the configuration source.
        """

    @abstractmethod
    async def write_async(self, content: AnyStr) -> int:
        """
        Write the configuration source file asynchronously
        and return the number of bytes written.

        Parameters
        ----------
        content
            The content to write to the configuration source.
        """


class BinaryFileConfigurationSource(FileConfigurationSource[bytes]):
    """Class for loading and saving configuration data from a binary file."""

    def load(self) -> Data:
        """
        Load the configuration source file and return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(BytesIO(self.read()))

    async def load_async(self) -> Data:
        """
        Load the configuration source file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(BytesIO(await self.read_async()))

    def dump(self, data: Data) -> int:
        """
        Load the configuration source binary file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = BytesIO()
        self.data_format.dump(data, stream)
        return self.write(stream.getvalue())

    async def dump_async(self, data: Data) -> int:
        """
        Load the configuration source file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = BytesIO()
        self.data_format.dump(data, stream)
        return await self.write_async(stream.getvalue())

    def read(self) -> bytes:
        """Read the configuration source file and return its contents (bytes)."""
        return self.source.read_bytes()

    async def read_async(self) -> bytes:
        """
        Read the configuration source asynchronously and return
        its contents (bytes).
        """
        return await AsyncPath(self.source).read_bytes()

    def write(self, content: bytes) -> int:
        """
        Write the configuration source and return the number of bytes written.

        Parameters
        ----------
        content
            The bytes to write to the configuration source.
        """
        return self.source.write_bytes(content)

    async def write_async(self, content: bytes) -> int:
        """
        Write the configuration source asynchronously
        and return the number of bytes written.

        Parameters
        ----------
        content
            The bytes to write to the configuration source.
        """
        return await AsyncPath(self.source).write_bytes(content)


class TextFileConfigurationSource(FileConfigurationSource[str]):
    """Class for loading and saving configuration data from a text file."""

    def load(self) -> Data:
        """
        Load the configuration source file and return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(StringIO(self.read()))

    async def load_async(self) -> Data:
        """
        Load the configuration source file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data_format
            The data format to use when loading the data.
        """
        return self.data_format.load(StringIO(await self.read_async()))

    def dump(self, data: Data) -> int:
        """
        Load the configuration source binary file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = StringIO()
        self.data_format.dump(data, stream)
        return self.write(stream.getvalue())

    async def dump_async(self, data: Data) -> int:
        """
        Load the configuration source file asynchronously
        and return its contents as a dictionary.

        Parameters
        ----------
        data
            The data to dump to the configuration source.
        data_format
            The data format to use when dumping the data.
        """
        stream = StringIO()
        self.data_format.dump(data, stream)
        return await self.write_async(stream.getvalue())

    def read(self) -> str:
        """Read the configuration source file and return its contents (str)."""
        return self.source.read_text()

    async def read_async(self) -> str:
        """
        Read the configuration source asynchronously and return
        its contents (str).
        """
        return await AsyncPath(self.source).read_text()

    def write(self, content: str) -> int:
        """
        Write the configuration source and return the number of bytes written.

        Parameters
        ----------
        content
            The string to write to the configuration source.
        """
        return self.source.write_text(content)

    async def write_async(self, content: str) -> int:
        """
        Write the configuration source asynchronously
        and return the number of bytes written.

        Parameters
        ----------
        content
            The string to write to the configuration source.
        """
        return await AsyncPath(self.source).write_text(content)


@overload
def get_configuration_source_file(
    source: str | bytes | PathLike[str] | PathLike[bytes],
    *,
    format_type: Literal["binary"],
) -> BinaryFileConfigurationSource:
    ...


@overload
def get_configuration_source_file(
    source: str | bytes | PathLike[str] | PathLike[bytes],
    *,
    format_type: Literal["text"],
) -> TextFileConfigurationSource:
    ...


@get_configuration_source.register(str)
@get_configuration_source.register(bytes)
@get_configuration_source.register(PathLike)
def get_configuration_source_file(
    source: str | bytes | PathLike[str] | PathLike[bytes],
    *,
    format_type: Literal["binary", "text", "auto"] = "auto",
) -> BinaryFileConfigurationSource | TextFileConfigurationSource:
    """Get a dedicated interface for a configuration source file."""
    if format_type == "binary":
        return BinaryFileConfigurationSource(source)
    if format_type == "text":
        return TextFileConfigurationSource(source)
    msg = f"Unknown source type: {format_type!r}, expected 'auto', 'binary' or 'text'"
    raise ValueError(msg)
