from __future__ import annotations

import functools
from typing import TYPE_CHECKING, ClassVar

from configzen.errors import InternalSyntaxError, formatted_syntax_error

if TYPE_CHECKING:
    from collections.abc import Iterator

    from configzen.typedefs import ConfigRouteLike

__all__ = ("ConfigRoute",)


class ConfigRoute:
    TOK_DOT: ClassVar[str] = "."
    TOK_ESCAPE: ClassVar[str] = "\\"
    TOK_DOTLISTESC_ENTER: ClassVar[str] = "["
    TOK_DOTLISTESC_EXIT: ClassVar[str] = "]"

    def __init__(self, route: ConfigRouteLike, *, allow_empty: bool = False) -> None:
        items = self.parse(route)
        if not (allow_empty or items):
            msg = "Empty configuration route"
            raise ValueError(msg)
        self.items = items

    @classmethod
    def parse(cls, route: ConfigRouteLike) -> list[str]:
        if isinstance(route, ConfigRoute):
            return route.items
        if isinstance(route, list):
            return route
        if isinstance(route, str):
            with formatted_syntax_error(route):
                return cls._decompose(route)
        msg = f"Invalid route type {type(route)!r}"
        raise TypeError(msg)

    @classmethod
    def _decompose(cls, route: str) -> list[str]:
        tok_dot = cls.TOK_DOT
        tok_escape = cls.TOK_ESCAPE
        tok_dle_enter = cls.TOK_DOTLISTESC_ENTER
        tok_dle_exit = cls.TOK_DOTLISTESC_EXIT

        if not route.endswith(tok_dot):
            route += tok_dot

        part = ""
        dle_ctx: int | None = None
        items: list[str] = []
        enter = items.append
        error = functools.partial(InternalSyntaxError, prefix="Route(", suffix=")")
        escape = False

        for index, char in enumerate(route):
            if escape:
                part += char
                escape = False
                continue
            is_last = index == len(route) - 1
            if char == tok_dot:
                if dle_ctx is not None:
                    part += char
                else:
                    enter(part)
                    part = ""
            elif char == tok_escape:
                if is_last:
                    part += char
                else:
                    escape = True
            elif char == tok_dle_enter:
                if dle_ctx is not None:
                    # a special character at its place
                    part += char
                else:
                    dle_ctx = index
            elif char == tok_dle_exit:
                if is_last or route[index + 1] == tok_dot:
                    if dle_ctx is None:
                        msg = (
                            "Dotlist escape sequence "
                            f"was not opened with {tok_dle_enter!r}"
                        )
                        raise error(msg, index=index)
                    dle_ctx = None
                else:
                    # a special character at its place
                    part += char
            else:
                part += char
            if is_last and dle_ctx is not None:
                msg = (
                    "Unclosed dotlist escape sequence "
                    f"(expected {tok_dle_exit!r} token)"
                )
                raise error(msg, index=dle_ctx)
        return items

    @classmethod
    def decompose(cls, route: str) -> list[str]:
        with formatted_syntax_error(route):
            return cls._decompose(route)

    def compose(self) -> str:
        escape = (self.TOK_DOTLISTESC_ENTER, self.TOK_DOTLISTESC_EXIT)
        raw = ("", "")
        return self.TOK_DOT.join(
            fragment.join(escape).replace(
                self.TOK_DOTLISTESC_EXIT + self.TOK_DOT,
                self.TOK_DOTLISTESC_EXIT + self.TOK_ESCAPE + self.TOK_DOT,
            )
            if self.TOK_DOT in fragment
            else fragment.join(raw)
            for fragment in self.items
        )

    def enter(self, subroute: ConfigRouteLike) -> ConfigRoute:
        return type(self)(self.items + self.parse(subroute))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ConfigRoute):
            return self.items == other.items
        if isinstance(other, str):
            return self.items == self.decompose(other)
        if isinstance(other, list):
            return self.items == other
        return NotImplemented

    def __str__(self) -> str:
        return self.compose()

    def __iter__(self) -> Iterator[str]:
        yield from self.items

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.items})"
