"""This module contains all the custom errors raised by _configzen_."""

from __future__ import annotations

import collections.abc
import contextlib
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from configzen.config import ConfigAgent
    from configzen.typedefs import ConfigModelT


class ConfigError(Exception):
    """An error occurred while loading a configuration."""


class IncorrectConfigError(ConfigError):
    """An error occurred while loading a configuration."""


class InternalSyntaxError(ConfigError):
    """Error in route syntax."""

    def __init__(
        self,
        msg: str,
        index: Any = None,
        prefix: str = "",
        suffix: str = "",
    ) -> None:
        super().__init__(msg)
        self.index = index
        self.prefix = prefix
        self.suffix = suffix


class ConfigSyntaxError(ConfigError):
    """An error occurred while parsing arguments."""


@contextlib.contextmanager
def formatted_syntax_error(source: str) -> collections.abc.Iterator[None]:
    """Raise a SyntaxError with a message and a source."""
    try:
        yield
    except InternalSyntaxError as exc:
        idx = len(exc.prefix) + exc.index + 1
        charlist = [" "] * len(exc.prefix + repr(source) + exc.suffix)
        charlist[idx] = "^"
        indicator = "".join(charlist)
        msg = "\n".join(
            map(str, (exc, exc.prefix + repr(source) + exc.suffix, indicator))
        )
        raise ConfigSyntaxError(msg) from None


class UnspecifiedParserError(ConfigError):
    """Could not determine the parser to use."""


class UnavailableParserError(ConfigError):
    MISSING_DEPENDENCIES: dict[str, str] = {
        "yaml": "pyyaml (or ruamel.yaml)",
        "toml": "toml",
        "ion": "anyconfig-ion-backend",
        "bson": "anyconfig-bson-backend",
        "msgpack": "anyconfig-msgpack-backend",
        "cbor": "anyconfig-cbor-backend (or anyconfig-cbor2-backend)",
        "configobj": "anyconfig-configobj-backend",
    }

    def __init__(self, parser_name: str, agent: ConfigAgent[ConfigModelT]) -> None:
        missing_dependency: str = self.MISSING_DEPENDENCIES.get(
            parser_name, f"the proper anyconfig backend for {parser_name} files"
        )
        super().__init__(
            f"The {parser_name!r} parser required to load configuration "
            f"for agent {agent} is not available.\n"
            f"Install it with `pip install {missing_dependency}`."
        )


class ConfigAccessError(ConfigError, LookupError):
    """An error occurred while accessing configuration part."""

    def __init__(self, config: ConfigModelT, route: str | list[str]) -> None:
        if not isinstance(route, str):
            route = ".".join(route)
        self.route = route
        super().__init__(
            f"could not get {type(config).__name__}.{route}",
        )


class ConfigProcessorError(ConfigError):
    """An error occurred while preprocessing/exporting a configuration."""


class ResourceLookupError(ConfigError, LookupError):
    """An error occurred while looking up a resource."""

    def __init__(
        self, resource: ConfigAgent[ConfigModelT] | None, route: list[str]
    ) -> None:
        resource_name = resource.resource if resource else "the provided resource"
        super().__init__(f"{route} not found in {resource_name}")


class ConfigPreprocessingError(ConfigProcessorError, ValueError):
    """An error occurred while preprocessing a configuration value."""
