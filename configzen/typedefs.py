# Never import `Configuration` outside of TYPE_CHECKING blocks.

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from configzen.configuration import BaseConfiguration

    Configuration = TypeVar("Configuration", bound=BaseConfiguration)
else:
    Configuration = TypeVar("Configuration", bound="BaseConfiguration")
