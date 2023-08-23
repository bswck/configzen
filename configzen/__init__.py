# flake8: noqa
# pyright: reportUnsupportedDunderAll=false
from __future__ import annotations

from pydantic import validator as field_validator

from . import decorators, field, interpolation, model, module, processor, route
from .decorators import *
from .field import *
from .interpolation import *
from .model import *
from .module import *
from .processor import *
from .route import *

__author__ = "bswck"
__copyright__ = "Copyright 2023–present, bswck"
__credits__ = ["Lunarmagpie", "trag1c"]
__version__ = "0.10.0"

__all__ = (
    *model.__all__,
    *field.__all__,
    *interpolation.__all__,
    *processor.__all__,
    *decorators.__all__,
    *route.__all__,
    *module.__all__,
    "field_validator",
)
