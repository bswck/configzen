"""This module contains all the custom errors raised by _configzen_."""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import anyconfig

if TYPE_CHECKING:
    from configzen.config import ConfigLoader
    from configzen.typedefs import ConfigModelT


class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class InternalConfigError(ConfigError):
    """Error to raise before reraising as an underlying error."""

    def __init__(self, msg: str, extra: Any = None) -> None:
        super().__init__(msg)
        self.extra = extra


class ArgumentSyntaxError(ConfigError):
    """An error occurred while parsing arguments."""


@contextlib.contextmanager
def format_syntax_error(source: str) -> Generator[None, None, None]:
    """Raise a SyntaxError with a message and a source."""
    try:
        yield
    except InternalConfigError as exc:
        char_no = exc.extra
        charlist = ["~"] * len(source)
        charlist[char_no] = "^"
        indicator = "".join(charlist)
        msg = "\n".join(map(str, (exc, repr(source), indicator)))
        raise ArgumentSyntaxError(msg) from None


class UnknownParserError(ConfigError, anyconfig.UnknownFileTypeError):
    """Engine was attempted to be used but is not registered."""


class ConfigItemAccessError(ConfigError, LookupError):
    """An error occurred while accessing configuration part."""

    def __init__(self, config: ConfigModelT, route: str | list[str]) -> None:
        if not isinstance(route, str):
            route = ".".join(route)
        super().__init__(
            f"could not get {type(config).__name__}.{route}",
        )


class ProcessorLookupError(ConfigError, LookupError):
    """An error occurred while looking up a processor."""

    def __init__(
        self,
        resource: ConfigLoader[ConfigModelT] | None,
        route: list[str]
    ) -> None:
        resource_name = resource.resource if resource else "the provided resource"
        super().__init__(f"{route} not found in {resource_name}")
