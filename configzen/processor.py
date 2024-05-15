"""
Replacement API processor for configuration data.

Allows to tweak the configuration data programmatically before it is given
to the model config and revert the changes back to the original data structure
when the configuration managed by that model is saved.
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from copy import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, TypedDict

from configzen.errors import ConfigProcessorError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import NewType

    from typing_extensions import TypeAlias

    from configzen.data import Data

    MacroDict: TypeAlias = "dict[str, Callable[[object], dict[str, object]]]"
    Char = NewType("Char", str)
else:

    def Char(s: str) -> str:  # noqa: N802
        if len(s) != 1:
            msg = "Char must be a single character"
            raise ValueError(msg)
        return s


__all__ = (
    "ConfigProcessor",
    "ProcessorOptions",
    "ProcessorReplacement",
)


class ProcessorOptions(TypedDict):
    """Prototype of the allowed options for the ConfigProcessor class."""

    macro_prefix: Char
    update_prefix: Char
    macros_on_top: bool


class _ProcessedReplacements:
    def __init__(self) -> None:
        self.__replacements: dict[str, ProcessorReplacement] = {}

    def items(self) -> Iterator[tuple[str, ProcessorReplacement]]:
        yield from self.__replacements.items()

    def update(self, substitute: dict[str, ProcessorReplacement]) -> None:
        replacements = self.__replacements
        if set(replacements) & set(substitute):  # what then?
            msg = "Replacement collision"
            raise ValueError(msg)
        replacements.update(substitute)


# Note: Add generic type params for 3.9+
class _ProcessedData(UserDict):  # type: ignore[type-arg]
    def __init__(
        self,
        *,
        data: MutableMapping[str, object],
        options: ProcessorOptions,
        macros: MacroDict,
    ) -> None:
        self.macros = macros
        self.options = options
        self.__replacements = _ProcessedReplacements()

        super().__init__()

        for key, value in data.items():
            replacement = self.find_replacement(key, value)
            if replacement is None:
                self.data[key] = value
                continue
            substitute = replacement.content
            self.data.pop(key)
            self.data.update(substitute)
            self.__replacements.update(dict.fromkeys(substitute, replacement))

    def find_replacement(
        self,
        key: str | None,
        value: object,
    ) -> ProcessorReplacement | None:
        """
        Find a replacement for a single item, for programmatic use.

        Return None if not found.
        """
        macro_prefix = self.options["macro_prefix"]
        update_prefix = self.options["update_prefix"]

        if not key:
            return None

        if key.startswith(macro_prefix):
            macro_name = key[len(macro_prefix) :].rstrip()
            try:
                macro = self.macros[macro_name]
            except KeyError as err:
                msg = f"No such macro: {macro_name!r}"
                raise ConfigProcessorError(msg) from err
            return ProcessorReplacement(
                key=key,
                value=value,
                # Note: Use str.removeprefix() for 3.9+
                content=macro(value),
            )

        if key.startswith(update_prefix):
            update_key = key[len(update_prefix) :].rstrip()
            return ProcessorReplacement(
                key=key,
                value=value,
                content={update_key: self.update_existing(update_key, value)},
            )

        return None

    def update_existing(self, key: str, value: object) -> object:
        """Update (NOT replace) a value for key with a new value."""
        missing = object()
        existent = self.get(key, missing)
        if existent is missing:
            return value
        substitute: object
        if isinstance(existent, MutableMapping) and isinstance(value, Mapping):
            substitute = copy(existent)
            substitute.update(value)
            return substitute
        if isinstance(existent, MutableSequence) and isinstance(value, Sequence):
            substitute = copy(existent)
            substitute.extend(value)
            return substitute
        msg = f"Cannot update {type(existent)} with {type(value)}"
        raise TypeError(msg)

    def revert_replacements(self) -> Data:
        """Revert all replacements and return the original data structure."""
        before_replacements = {}
        skip_keys = []
        for key, replacement in self.__replacements.items():
            before_replacements[replacement.key] = replacement.value
            skip_keys.append(key)
        for key, value in self.data.items():
            if key not in skip_keys:
                before_replacements[key] = value
        return before_replacements


class ConfigProcessor:
    """
    A class that takes in configuration data and processes it.

    Recursively resolves & applies replacements in data magically.
    """

    _get_processed_data: Callable[..., _ProcessedData] = _ProcessedData

    macros: ClassVar[MacroDict] = {
        "extend": lambda x: x,
    }

    def __init__(
        self,
        initial: Data,
        *,
        macro_prefix: Char = Char("^"),  # noqa: B008
        update_prefix: Char = Char("+"),  # noqa: B008
        macros_on_top: bool = False,
    ) -> None:
        self.__initial = initial
        self.__data: _ProcessedData = None  # type: ignore[assignment]

        self.options = ProcessorOptions(
            macro_prefix=macro_prefix,
            update_prefix=update_prefix,
            macros_on_top=macros_on_top,
        )

    @property
    def roundtrip_initial(self) -> Data:
        """The initial configuration data that the processor was given."""
        return self.__initial

    def create_processor(self, data: Data) -> ConfigProcessor:
        """Create a new configuration processor with identical options."""
        return type(self)(data, **self.options)

    def get_processed_data(
        self,
        *,
        force: bool = False,
    ) -> _ProcessedData:
        """
        Create the data with replacements or return the one already cached.

        Parameters
        ----------
        force
            Whether to forcibly parse the original data even if it was already parsed.
            Default is False.

        """
        if force or self.__data is None:
            self.__data = self._get_processed_data(
                data=self.__initial,
                options=self.options,
                macros=self.macros,
            )
        return self.__data


@dataclass
class ProcessorReplacement:
    """
    A change that was made to the configuration data during processing.

    Attributes
    ----------
    key
        The key of the item before alteration.
    value
        The value of the item before alteration.
    content
        The value to unpack in place of the alteration key.

    """

    key: str
    value: object
    content: dict[str, object]
