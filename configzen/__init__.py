# flake8: noqa
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import validator as field_validator

from . import config, decorators, field, processor, route
from .config import *
from .decorators import *
from .field import *
from .processor import *
from .route import *

if TYPE_CHECKING:
    from typing import Any

__all__ = (
    *config.__all__,
    *field.__all__,
    *processor.__all__,
    *decorators.__all__,
    *route.__all__,
    "field_validator",
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
