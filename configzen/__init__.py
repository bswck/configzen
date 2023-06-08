# flake8: noqa
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

from pydantic import Field as ConfigField

from . import config, processor
from .config import *
from .processor import *

__all__ = (
    *config.__all__,
    *processor.__all__,
    "ConfigField",
)


def __getattr__(name: str) -> Any:
    if name == "ConfigManager":
        import warnings

        warnings.warn(
            "``configzen.ConfigManager`` is deprecated, "
            "import ``configzen.ConfigAgent`` instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return ConfigAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


del annotations, TYPE_CHECKING
