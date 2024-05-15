"""Specialized exceptions raised by configzen."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from configzen.config import BaseConfig


__all__ = (
    "ConfigError",
    "ConfigLoadError",
    "ConfigReloadError",
    "ConfigSaveError",
    "NotAMappingError",
    "ConfigProcessorError",
    "RouteError",
    "LinkedRouteError",
)


class ConfigError(Exception):
    """Base class for all errors related to configzen."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigLoadError(ConfigError):
    """Raised when the configuration cannot be loaded."""


class ConfigReloadError(ConfigLoadError):
    """Raised when the configuration cannot be reloaded."""


class ConfigSaveError(ConfigError):
    """Raised when the configuration cannot be saved."""


class NotAMappingError(ConfigLoadError, TypeError):
    """Raised when the configuration being loaded is not a mapping."""


class ConfigProcessorError(ConfigError):
    """Raised when a configuration replacement processor error occurs."""


class BaseRouteError(ConfigError, ValueError):
    """Raised when a configuration item route is invalid."""


class RouteError(BaseRouteError):
    """Raised when a configuration item route is invalid at a specific index."""

    def __init__(self, message: str, route: str, index: int) -> None:
        self.message = message
        self.route = route
        self.index = index

    def __str__(self) -> str:
        """Return a string representation of the route error."""
        return f"{self.message} ({self.route}:{self.index})"


class LinkedRouteError(BaseRouteError):
    """Raised when a declared configuration item route is invalid."""

    def __init__(
        self,
        message: str,
        route: str,
        config_class: type[BaseConfig],
    ) -> None:
        self.message = message
        self.route = route
        self.config_class = config_class

    def __str__(self) -> str:
        """Return a string representation of the route error."""
        return f"{self.message} ({self.config_class.__name__}.{self.route})"
