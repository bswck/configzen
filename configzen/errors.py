class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncompleteConfigurationError(ConfigError):
    """An error occurred while loading a configuration."""
