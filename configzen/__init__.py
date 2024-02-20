# SPDX-License-Identifier: GPL-3.0
# (C) 2024-present Bartosz Sławecki (bswck)
"""
`configzen`.

Manage configuration with pydantic.
"""

from pydantic import Field

from . import configuration, errors, formats, module_proxy, routes, sources
from .configuration import *  # noqa: F403
from .errors import *  # noqa: F403
from .formats import *  # noqa: F403
from .module_proxy import *  # noqa: F403
from .routes import *  # noqa: F403
from .sources import *  # noqa: F403

__all__ = (  # noqa: PLE0604
    "Field",
    *configuration.__all__,
    *errors.__all__,
    *formats.__all__,
    *module_proxy.__all__,
    *routes.__all__,
    *sources.__all__,
)
