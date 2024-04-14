"""`configzen.formats.std_json`: The JSON data format."""

from __future__ import annotations

from json import JSONDecoder, JSONEncoder, dump, load
from typing import IO, TYPE_CHECKING, ClassVar, cast

from runtime_generics import runtime_generic

from configzen.data import DataFormatOptions, TextDataFormat

if TYPE_CHECKING:
    from collections.abc import Callable

    from typing_extensions import Unpack

    from configzen.data import Data


__all__ = (
    "JSONDataFormat",
    "JSONOptions",
)


class JSONOptions(DataFormatOptions, total=False):
    """Prototype of the allowed options for the JSON data format."""

    object_hook: Callable[[dict[str, object]], object] | None  # JSONDecoder
    parse_float: Callable[[str], float] | None  # JSONDecoder
    parse_int: Callable[[str], int] | None  # JSONDecoder
    parse_constant: Callable[[str], object] | None  # JSONDecoder
    strict: bool  # JSONDecoder
    object_pairs_hook: (
        Callable[
            [list[tuple[str, object]]],
            object,
        ]
        | None
    )  # JSONDecoder

    skipkeys: bool  # JSONEncoder
    ensure_ascii: bool  # JSONEncoder
    check_circular: bool  # JSONEncoder
    allow_nan: bool  # JSONEncoder
    sort_keys: bool  # JSONEncoder
    indent: int | str | None  # JSONEncoder
    separators: tuple[str, str] | None  # JSONEncoder
    default: Callable[..., object] | None  # JSONEncoder


@runtime_generic
class JSONDataFormat(TextDataFormat[JSONOptions]):
    """The JSON data format."""

    option_name: ClassVar[str] = "json"

    # Subclass and override for global effect.
    json_encoder: JSONEncoder = JSONEncoder()
    json_decoder: JSONDecoder = JSONDecoder()

    default_extension: ClassVar[str] = "json"

    def configure(self, **options: Unpack[JSONOptions]) -> None:
        """For the documentation of the options, see the JSONOptions class."""
        self.json_encoder = JSONEncoder(
            skipkeys=options.get("skipkeys") or self.json_encoder.skipkeys,
            ensure_ascii=options.get("ensure_ascii") or self.json_encoder.ensure_ascii,
            check_circular=options.get("check_circular")
            or self.json_encoder.check_circular,
            allow_nan=options.get("allow_nan") or self.json_encoder.allow_nan,
            indent=options.get("indent") or self.json_encoder.indent,
            separators=options.get("separators")
            or (
                self.json_encoder.item_separator,
                self.json_encoder.key_separator,
            ),
            default=options.get("default") or self.json_encoder.default,
        )
        self.json_decoder = JSONDecoder(
            object_hook=options.get("object_hook") or self.json_decoder.object_hook,
            parse_float=options.get("parse_float") or self.json_decoder.parse_float,
            parse_int=options.get("parse_int") or self.json_decoder.parse_int,
            parse_constant=options.get("parse_constant")
            or self.json_decoder.parse_constant,
            strict=options.get("strict") or self.json_decoder.strict,
            object_pairs_hook=options.get("object_pairs_hook")
            or self.json_decoder.object_pairs_hook,
        )

    def load(self, stream: IO[str]) -> Data:
        """Load the JSON data from the given stream."""
        document = (
            load(
                stream,
                cls=cast("type[JSONDecoder]", lambda **_: self.json_decoder),
            )
            or {}
        )
        if not isinstance(document, dict):
            msg = (
                f"Expected a dict mapping, "
                f"but got {type(document).__name__} instead."
            )
            raise TypeError(msg)
        return document

    def dump(self, data: Data, stream: IO[str]) -> None:
        """Dump the given JSON data to the given stream."""
        dump(
            data,
            stream,
            cls=cast("type[JSONEncoder]", lambda **_: self.json_encoder),
        )
