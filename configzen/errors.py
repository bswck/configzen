class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncompleteConfigurationError(ConfigError):
    """An error occurred while loading a configuration."""


class MissingEngineError(ConfigError):
    """Engine was attempted to be used but is not installed."""
