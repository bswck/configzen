# SPDX-License-Identifier: GPL-3.0
# (C) 2024-present Bartosz Sławecki (bswck)
"""Manage configuration with pydantic."""

from __future__ import annotations

from pydantic import Field

from . import config, data, errors, formats, module_proxy, routes, sources
from .config import *  # noqa: F403
from .data import *  # noqa: F403
from .errors import *  # noqa: F403
from .formats import *  # noqa: F403
from .module_proxy import *  # noqa: F403
from .routes import *  # noqa: F403
from .sources import *  # noqa: F403

__all__ = (  # noqa: PLE0604
    "Field",
    *config.__all__,
    *data.__all__,
    *errors.__all__,
    *formats.__all__,
    *module_proxy.__all__,
    *routes.__all__,
    *sources.__all__,
)
