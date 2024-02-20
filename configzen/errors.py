"""`configzen.errors`: Specialized exceptions raised by configzen."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from configzen.configuration import BaseConfiguration


__all__ = (
    "ConfigurationError",
    "ConfigurationLoadError",
    "ConfigurationReloadError",
    "ConfigurationSaveError",
    "NotAMappingError",
    "RouteError",
    "LinkedRouteError",
)


class ConfigurationError(Exception):
    """Base class for all errors related to configzen."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigurationLoadError(ConfigurationError):
    """Raised when the configuration cannot be loaded."""


class ConfigurationReloadError(ConfigurationLoadError):
    """Raised when the configuration cannot be reloaded."""


class ConfigurationSaveError(ConfigurationError):
    """Raised when the configuration cannot be saved."""


class NotAMappingError(ConfigurationLoadError, TypeError):
    """Raised when the configuration being loaded is not a mapping."""


class BaseRouteError(ConfigurationError, ValueError):
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
    """Raised when a configuration item route is invalid."""

    def __init__(
        self,
        message: str,
        route: str,
        configuration_class: type[BaseConfiguration],
    ) -> None:
        self.message = message
        self.route = route
        self.configuration_class = configuration_class

    def __str__(self) -> str:
        """Return a string representation of the route error."""
        return f"{self.message} ({self.configuration_class.__name__}.{self.route})"
