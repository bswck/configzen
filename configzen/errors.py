"""This module contains all the custom errors raised by _configzen_."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyconfig

if TYPE_CHECKING:
    from configzen.config import ConfigModelT, ConfigResource


class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


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


class ConfigParserLookupError(ConfigError, LookupError):
    """An error occurred while looking up a parser."""

    def __init__(self, resource: ConfigResource | None, route: list[str]) -> None:
        resource_name = resource.resource if resource else "the provided resource"
        super().__init__(f"{route} not found in {resource_name}")
