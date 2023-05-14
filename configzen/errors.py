"""This module contains all the custom errors raised by _configzen_."""

from __future__ import annotations

import typing

import anyconfig

if typing.TYPE_CHECKING:
    from configzen.config import ConfigModelBaseT


class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class UnknownParserError(ConfigError, anyconfig.UnknownFileTypeError):
    """Engine was attempted to be used but is not registered."""


class ConfigItemAccessError(ConfigError, LookupError):
    """An error occurred while accessing configuration part."""

    def __init__(self, config: ConfigModelBaseT, route: str | list[str]) -> None:
        if not isinstance(route, str):
            route = ".".join(route)
        super().__init__(
            f"could not get {type(config).__name__}.{route}",
        )
