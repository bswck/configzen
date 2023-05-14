import anyconfig


class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class UnknownParserError(ConfigError, anyconfig.UnknownFileTypeError):
    """Engine was attempted to be used but is not registered."""


class ConfigItemAccessError(ConfigError, LookupError):
    """An error occurred while accessing configuration part."""
    def __init__(self, config, route: str | list[str]):
        if not isinstance(route, str):
            route = ".".join(route)
        super().__init__(
            f"could not get {type(config).__name__}.{route}"
        )

