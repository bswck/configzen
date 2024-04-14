"""`configzen.formats.std_plist`: The Plist data format."""

from __future__ import annotations

from plistlib import PlistFormat, dump, load
from typing import IO, TYPE_CHECKING, Any, ClassVar

from runtime_generics import runtime_generic

from configzen.data import BinaryDataFormat, DataFormatOptions

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from typing_extensions import Unpack

    from configzen.data import Data


__all__ = (
    "PlistDataFormat",
    "PlistOptions",
)


class PlistOptions(DataFormatOptions, total=False):
    """Prototype of the allowed options for the Plist data format."""

    fmt: PlistFormat
    dict_type: type[MutableMapping[str, Any]]
    sort_keys: bool
    skipkeys: bool


@runtime_generic
class PlistDataFormat(BinaryDataFormat[PlistOptions]):
    """The Plist data format."""

    option_name: ClassVar[str] = "plist"

    # Subclass and override for global effect.
    plist_options: PlistOptions = PlistOptions()

    default_extension: ClassVar[str] = "plist"

    def configure(self, **options: Unpack[PlistOptions]) -> None:
        """For the documentation of the options, see the PlistOptions class."""
        if "fmt" not in options:
            options["fmt"] = self.plist_options.get("fmt", PlistFormat.FMT_XML)
        if "dict_type" not in options:
            options["dict_type"] = self.plist_options.get(
                "dict_type",
                dict,
            )  # type: ignore[typeddict-item]
        if "sort_keys" not in options:
            # configzen focuses on preserving the original structure,
            # so we don't sort by default.
            options["sort_keys"] = self.plist_options.get("sort_keys", False)
        if "skipkeys" not in options:
            options["skipkeys"] = self.plist_options.get("skipkeys", False)
        self.plist_options = options

    def load(self, stream: IO[bytes]) -> Data:
        """Load the data from the given stream."""
        dict_class: type[MutableMapping[str, Any]] = self.plist_options["dict_type"]
        document = (
            load(
                stream,
                fmt=self.plist_options["fmt"],
                dict_type=dict_class,
            )
            or dict_class()
        )
        if not isinstance(document, dict_class):
            msg = (
                f"Expected a {dict_class.__name__} mapping, "
                f"but got {type(document).__name__} instead."
            )
            raise TypeError(msg)
        return document

    def dump(self, data: Data, stream: IO[bytes]) -> None:
        """Dump the given data to the stream."""
        dump(
            data,
            stream,
            fmt=self.plist_options["fmt"],
            sort_keys=self.plist_options["sort_keys"],
            skipkeys=self.plist_options["skipkeys"],
        )
