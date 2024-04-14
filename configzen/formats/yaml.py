"""`configzen.formats.yaml`: The YAML data format."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from runtime_generics import runtime_generic

from configzen.data import DataFormatOptions, TextDataFormat

if TYPE_CHECKING:
    from typing import IO

    from typing_extensions import TypeAlias, Unpack

    from configzen.data import Data

__all__ = (
    "YAMLDataFormat",
    "YAMLOptions",
)


YAMLVersion: TypeAlias = "list[int] | str | tuple[int, int]"
YAMLTyp: TypeAlias = Literal["rt", "rtsc", "safe", "unsafe", "base"]


class YAMLOptions(DataFormatOptions, total=False):
    """
    Prototype of the allowed options for the YAML data format.

    For more information, see the documentation of the `ruamel.yaml.YAML` class.
    """

    typ: YAMLTyp | list[YAMLTyp] | None
    pure: bool
    plug_ins: list[str]

    classes: list[type]
    """List of classes to automatically call YAML.register_class() on."""

    version: str | tuple[int | str, ...]
    """The YAML version to use."""

    indent: int
    """Indentation width."""

    block_seq_indent: int
    """Indentation for nested block sequences."""


@runtime_generic
class YAMLDataFormat(TextDataFormat[YAMLOptions]):
    """The YAML data format."""

    option_name: ClassVar[str] = "yaml"

    # Subclass and override for global effect.
    yaml: YAML = YAML()

    default_extension: ClassVar[str] = "yml"
    file_extensions: ClassVar[set[str]] = {"yaml"}

    def configure(self, **options: Unpack[YAMLOptions]) -> None:
        """For the documentation of the options, see the YAMLOptions class."""
        yaml_classes = options.pop("classes", None) or []

        old_yaml = self.yaml
        yaml_version = options.pop("version", None) or old_yaml.version
        yaml_indent = options.pop("indent", None) or old_yaml.old_indent
        yaml_block_seq_indent = (
            options.pop("block_seq_indent", None) or old_yaml.block_seq_indent
        )

        yaml = YAML(**options)  # type: ignore[arg-type,misc]
        yaml.version = yaml_version  # type: ignore[assignment]
        yaml.indent = yaml_indent
        yaml.block_seq_indent = yaml_block_seq_indent

        for cls in yaml_classes:
            yaml.register_class(cls)

        self.yaml = yaml

    def load(self, stream: IO[str]) -> Data:
        """
        Load the data from a stream.

        Return a mutable mapping representing the loaded data
        which is mutation-sensitive (for round-trip processing).

        Every configuration source transforms the input data into a stream
        to be processed by the data format, because most data format libraries
        operate on streams.

        This method is called by the configuration model.
        """
        data = self.yaml.load(stream) or CommentedMap()
        if not isinstance(data, dict):
            msg = f"Expected a dict, but got {type(data).__name__}."
            raise TypeError(msg)
        return data

    def dump(self, data: Data, stream: IO[str]) -> None:
        """
        Load the data from a stream.

        Every configuration source transforms the input data into a stream
        to be processed by the data format, because most data format libraries
        operate on streams.

        This method is called by the configuration model.
        """
        self.yaml.dump(data, stream)
