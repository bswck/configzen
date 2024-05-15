"""Interfaces for encapsulation of configuring and using data formats."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from functools import partial
from itertools import zip_longest
from typing import (
    TYPE_CHECKING,
    Any,
    AnyStr,
    Generic,
    Literal,
    TypedDict,
    TypeVar,
    cast,
)

from runtime_generics import runtime_generic, type_check

if TYPE_CHECKING:
    from typing import IO, ClassVar, overload

    from typing_extensions import TypeAlias, Unpack

    from configzen.sources import ConfigSource

    Data: TypeAlias = MutableMapping[str, object]


__all__ = (
    "DataFormat",
    "DataFormatOptions",
    "DataFormatOptionsType",
    "BinaryDataFormat",
    "TextDataFormat",
)


class DataFormatOptions(TypedDict, total=False):
    """Base class for indicating possible options to configure a data format."""


DataFormatOptionsType = TypeVar("DataFormatOptionsType", bound=DataFormatOptions)


@runtime_generic
class DataFormat(Generic[DataFormatOptionsType, AnyStr], metaclass=ABCMeta):
    """
    Core interface for configuring and using any data format through within configzen.

    Do not use this class directly.
    If you need to implement your own data format, implement a subclass of:
    - BinaryDataFormat, if it is a bitwise data format, or
    - TextDataFormat, if it is a text data format.
    """

    extension_registry: ClassVar[dict[str, type[DataFormat[Any, Any]]]] = {}

    default_extension: ClassVar[str]
    file_extensions: ClassVar[set[str]]
    option_name: ClassVar[str]

    def __init__(self, options: DataFormatOptionsType | None = None) -> None:
        self.configure(**(options or {}))

    @classmethod
    def for_extension(
        cls,
        extension_name: str,
        options: DataFormatOptionsType | None = None,
    ) -> DataFormat[Any, Any]:
        """Create a data format instance for an extension."""
        return cls.extension_registry[extension_name](options)

    if TYPE_CHECKING:

        @overload
        def is_binary(
            self: DataFormat[DataFormatOptionsType, bytes],
        ) -> Literal[True]: ...

        @overload
        def is_binary(
            self: DataFormat[DataFormatOptionsType, str],
        ) -> Literal[False]: ...

    def is_binary(self) -> bool:
        """Return whether the data format is bitwise."""
        return type_check(self, DataFormat[Any, bytes])

    # Unpack[DataFormatOptionsType] cannot be used here,
    # because this functionality is not supported by mypy yet.
    # Override the **options annotation in your subclass of DataFormat with
    # the subclass of DataFormatOptions corresponding to your subclass of DataFormat.
    def configure(self, **options: Unpack[DataFormatOptions]) -> None:
        """
        Configure the data format.

        Every data format provides its own options, related to comments, indentation,
        and other format-specific features.
        """

    @abstractmethod
    def load(self, stream: IO[AnyStr]) -> Data:
        """
        Load the data from a stream.

        Return a mutable mapping representing the loaded data
        which is mutation-sensitive (for round-trip processing).

        Every configuration source transforms the input data into a stream
        to be processed by the data format, because most data format libraries
        operate on streams.

        This method is called by the config instance.
        """

    @abstractmethod
    def dump(self, data: Data, stream: IO[AnyStr]) -> None:
        """
        Load the data from a stream.

        Every configuration source transforms the input data into a stream
        to be processed by the data format, because most libraries implementing
        data formats operate on streams.

        This method is called by the config instance.
        """

    @classmethod
    def register_file_extensions(cls) -> None:
        """Register the file extensions supported by this data format."""
        cls.extension_registry.update(dict.fromkeys(cls.file_extensions, cls))

    def validate_source(self, source: ConfigSource[Any, AnyStr]) -> None:
        """Validate the config source."""
        if self.is_binary() and not source.is_binary():
            msg = (
                f"{source} is not a binary source, "
                f"but {self.__class__.__name__} is a binary data format"
            )
            raise TypeError(msg)

    def roundtrip_update_mapping(
        self,
        roundtrip_data: Data,
        mergeable_data: MutableMapping[str, object],
    ) -> None:
        """
        Update the loaded data in a round-trip manner.

        Use values from the configuration altered programmatically in runtime,
        while keeping the structure and comments of the original data.

        Parameters
        ----------
        roundtrip_data
            The data to be updated. Stores the original structure, comments etc.
        mergeable_data
            The new values to be merged into the loaded data.

        """
        return roundtrip_update_mapping(
            roundtrip_data,
            mergeable_data,
            _recursive_update_mapping=self.roundtrip_update_mapping,
            _recursive_update_sequence=self.roundtrip_update_sequence,
        )

    def roundtrip_update_sequence(
        self,
        roundtrip_data: MutableSequence[object],
        mergeable_data: Sequence[object],
    ) -> None:
        """Merge new data sequence without losing comments."""
        return roundtrip_update_sequence(
            roundtrip_data,
            mergeable_data,
            _recursive_update_mapping=self.roundtrip_update_mapping,
            _recursive_update_sequence=self.roundtrip_update_sequence,
        )

    def __init_subclass__(cls, *, skip_hook: bool = False) -> None:
        """Subclass hook. Pass skip_hook=True to skip it."""
        if not skip_hook:
            if getattr(cls, "option_name", None) is None:
                msg = (
                    f"{cls.__name__} must have an option_name attribute "
                    "if it is not a class with skip_hook=True parameter"
                )
                raise TypeError(msg)
            if getattr(cls, "file_extensions", None) is None:
                cls.file_extensions = set()
            cls.file_extensions.add(cls.default_extension)
            cls.register_file_extensions()


BinaryDataFormat = DataFormat[DataFormatOptionsType, bytes]
"""
Core interface for configuring and using binary data formats through
within configzen.

Do not use this class directly.
If you need to implement your own binary data format,
implement a subclass of this class.
Remember to ensure that your subclass is executed, so that it gets registered
to the registry of data formats.
"""


TextDataFormat = DataFormat[DataFormatOptionsType, str]
"""
Core interface for configuring and using text data formats through
within configzen.

Do not use this class directly.
If you need to implement your own text data format,
implement a subclass of this class.
Remember to ensure that your subclass is executed, so that it gets registered
to the registry of data formats.
"""


def roundtrip_update_mapping(
    roundtrip_data: Data,
    mergeable_data: MutableMapping[str, object],
    *,
    _recursive_update_mapping: Callable[[Data, MutableMapping[str, object]], None]
    | None = None,
    _recursive_update_sequence: Callable[
        [MutableSequence[object], Sequence[object]],
        None,
    ]
    | None = None,
) -> None:
    """Update a mapping without losing recursively attached metadata."""
    if _recursive_update_mapping is None:
        _recursive_update_mapping = partial(
            roundtrip_update_mapping,
            _recursive_update_sequence=_recursive_update_sequence,
        )
    if _recursive_update_sequence is None:
        _recursive_update_sequence = partial(
            roundtrip_update_sequence,
            _recursive_update_mapping=_recursive_update_mapping,
        )
    for key, value in roundtrip_data.items():
        if key in mergeable_data:
            new_value = mergeable_data.pop(key)
            if isinstance(value, MutableMapping):
                # Coerce it's a dict to ensure it has the .pop() method
                _recursive_update_mapping(
                    value,
                    dict(cast("Mapping[str, object]", new_value)),
                )
            elif isinstance(value, MutableSequence):
                _recursive_update_sequence(
                    value,
                    cast("MutableSequence[object]", new_value),
                )
            else:
                roundtrip_data[key] = new_value
    for key, value in mergeable_data.items():
        roundtrip_data[key] = value


def roundtrip_update_sequence(
    roundtrip_data: MutableSequence[object],
    mergeable_data: Sequence[object],
    *,
    _recursive_update_mapping: Callable[[Data, MutableMapping[str, object]], None]
    | None = None,
    _recursive_update_sequence: Callable[
        [MutableSequence[object], Sequence[object]],
        None,
    ]
    | None = None,
) -> None:
    """Update a sequence without losing recursively attached metadata."""
    if _recursive_update_mapping is None:
        _recursive_update_mapping = partial(
            roundtrip_update_mapping,
            _recursive_update_sequence=_recursive_update_sequence,
        )
    if _recursive_update_sequence is None:
        _recursive_update_sequence = partial(
            roundtrip_update_sequence,
            _recursive_update_mapping=_recursive_update_mapping,
        )
    sequence_length = len(mergeable_data)
    for i, (roundtrip_item, mergeable_item) in enumerate(
        zip_longest(
            roundtrip_data,
            mergeable_data,
        ),
    ):
        if i >= sequence_length:
            roundtrip_data[i] = roundtrip_item
        elif isinstance(roundtrip_item, MutableMapping):
            _recursive_update_mapping(
                roundtrip_item,
                dict(cast("Mapping[str, object]", mergeable_item)),
            )
        elif isinstance(roundtrip_item, MutableSequence):
            _recursive_update_sequence(
                roundtrip_item,
                cast("list[object]", mergeable_item),
            )
