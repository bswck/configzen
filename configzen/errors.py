"""Specialized exceptions raised by configzen."""

__all__ = (
    "ConfigurationError",
    "ConfigurationLoadError",
    "ConfigurationReloadError",
    "ConfigurationSaveError",
    "NotAMappingError",
    "RouteError",
)


class ConfigurationError(Exception):
    """Base class for all errors related to configuration management with configzen."""

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


class RouteError(ConfigurationError, ValueError):
    """Raised when a configuration item route is invalid."""

    def __init__(self, message: str, route: str, index: int) -> None:
        self.message = message
        self.route = route
        self.index = index

    def __str__(self) -> str:
        """Return a string representation of the route error."""
        return f"{self.message} ({self.route}:{self.index})"
