"""All the custom errors raised by _configzen_."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

    from configzen.model import ConfigAgent, ConfigModel
    from configzen.typedefs import ConfigModelT


class ConfigError(Exception):
    """An error occurred while loading a configuration."""

    def __init__(self, message: str) -> None:
        self._message = message
        super().__init__(message)

    @property
    def message(self) -> str:
        """The error message."""
        return self._message

    @message.setter
    def message(self, value: str) -> None:
        self._message = value
        super().__init__(self.message)


class InterpolationError(ConfigError):
    """An error occurred with regard to interpolating a configuration."""


class InterpolationLookupError(ConfigError, LookupError):
    """An error occurred with regard to interpolating a configuration."""

    def __init__(self, message: str) -> None:
        super().__init__(repr(message))


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class InternalSyntaxError(ConfigError):
    """Syntax error in a _configzen_ component."""

    def __init__(
        self,
        message: str,
        index: Any = None,
        prefix: str = "",
        suffix: str = "",
    ) -> None:
        super().__init__(message)
        self.index = index
        self.prefix = prefix
        self.suffix = suffix


class ConfigSyntaxError(ConfigError):
    """An error occurred while parsing arguments."""


@contextlib.contextmanager
def formatted_syntax_error(
    source: str,
    error_cls: type[ConfigSyntaxError] = ConfigSyntaxError,
) -> Iterator[None]:
    """Raise a SyntaxError with a message and a source."""
    try:
        yield
    except InternalSyntaxError as exc:
        idx = len(exc.prefix) + exc.index + 1
        charlist = [" "] * len(exc.prefix + repr(source) + exc.suffix)
        charlist[idx] = "^"
        indicator = "".join(charlist)
        msg = "\n".join(
            map(str, (exc, exc.prefix + repr(source) + exc.suffix, indicator)),
        )
        raise error_cls(msg) from None


class UnspecifiedParserError(ConfigError):
    """Could not determine the parser to use."""


class UnavailableParserError(ConfigError):
    MISSING_DEPENDENCIES: ClassVar[dict[str, str]] = {
        "yaml": "pyyaml (or ruamel.yaml)",
        "toml": "toml",
        "ion": "anyconfig-ion-backend",
        "bson": "anyconfig-bson-backend",
        "msgpack": "anyconfig-msgpack-backend",
        "cbor": "anyconfig-cbor2-backend (or anyconfig-cbor-backend)",
        "configobj": "anyconfig-configobj-backend",
    }

    def __init__(self, parser_name: str, agent: ConfigAgent[ConfigModelT]) -> None:
        missing_dependency: str = self.MISSING_DEPENDENCIES.get(
            parser_name,
            f"<the proper anyconfig backend for {parser_name!r} files>",
        )
        super().__init__(
            f"The {parser_name!r} parser required to load configuration "
            f"for agent {agent} is not available.\n"
            f"Install it with `pip install {missing_dependency}`.",
        )


class ConfigAccessError(ConfigError, LookupError):
    """An error occurred while accessing configuration part."""

    def __init__(self, config: ConfigModel, route: str | list[str]) -> None:
        if not isinstance(route, str):
            route = ".".join(route)
        self.route = route
        super().__init__(
            f"Could not access {type(config).__name__}.{route}",
        )


class ConfigProcessorError(ConfigError):
    """An error occurred while preprocessing/exporting a configuration."""


class ResourceLookupError(ConfigError, LookupError):
    """An error occurred while looking up a resource."""

    def __init__(
        self,
        agent: ConfigAgent[ConfigModelT] | None,
        route: list[str],
    ) -> None:
        resource_name = agent.resource if agent else "the provided resource"
        self.route = route
        super().__init__(f"{route} not found in {resource_name}")


class ConfigPreprocessingError(ConfigProcessorError, ValueError):
    """An error occurred while preprocessing a configuration value."""
