class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class UninstalledEngineError(ConfigError):
    """Engine was attempted to be used but is not installed."""

    def __init__(
        self,
        engine_name: str,
        *,
        library_name: str,
        installable_as_extra: bool = True,
    ) -> None:
        self.name = engine_name
        msg = f"{engine_name} engine requires {library_name} to be installed."
        if installable_as_extra:
            msg += (
                " You can install it as an extra with "
                f"`pip install configzen[{engine_name}]`."
            )
        super().__init__(msg)
