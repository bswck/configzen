"""`configzen.formats.toml`: The TOML data format."""

from __future__ import annotations

from typing import IO, TYPE_CHECKING, ClassVar

from runtime_generics import runtime_generic
from tomlkit.api import dump, load, register_encoder, unregister_encoder

from configzen.data import DataFormatOptions, TextDataFormat

if TYPE_CHECKING:
    from tomlkit.items import Encoder
    from typing_extensions import Unpack

    from configzen.data import Data


__all__ = (
    "TOMLDataFormat",
    "TOMLOptions",
)


class TOMLOptions(DataFormatOptions, total=False):
    """Prototype of the allowed options for the TOML data format."""

    encoders: list[Encoder]
    """List of encoders to perform automatic tomlkit.register_encoder() calls on."""

    unregister_old_encoders: bool
    """
    Whether to unregister all previously registered encoders
    before registering the new ones.
    """

    sort_keys: bool
    """
    Whether to sort keys in the output.
    """


@runtime_generic
class TOMLDataFormat(TextDataFormat[TOMLOptions]):
    """The TOML data format."""

    option_name: ClassVar[str] = "toml"

    # Subclass and override for global effect.
    toml_options: TOMLOptions = TOMLOptions()

    default_extension: ClassVar[str] = "toml"
    file_extensions: ClassVar[set[str]] = {"ini", "conf"}

    def configure(self, **options: Unpack[TOMLOptions]) -> None:
        """For the documentation of the options, see the TOMLOptions class."""
        old_options = self.toml_options
        toml_encoders = options.get("encoders") or old_options.get("encoders") or []
        cleanup_old_encoders = options.get("cleanup_old_encoders", False)

        if cleanup_old_encoders:
            for encoder in self.toml_options.get("encoders") or []:
                unregister_encoder(encoder)

        for encoder in toml_encoders:
            register_encoder(encoder)

    def load(self, stream: IO[str]) -> Data:
        """Load the data from the given stream."""
        return load(stream)

    def dump(self, data: Data, stream: IO[str]) -> None:
        """Dump the data to the given stream."""
        dump(
            data,
            stream,
            sort_keys=self.toml_options.get("sort_keys", False),
        )
