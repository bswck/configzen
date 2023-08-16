import contextlib
import os
import pathlib
import sys
from typing import TYPE_CHECKING, Any, List, Optional, TextIO, TypeVar, Union

if sys.version_info < (3, 9):
    from typing import Mapping
    from typing import Set as AbstractSet
else:
    from collections.abc import Mapping
    from collections.abc import Set as AbstractSet

if sys.version_info >= (3, 10):
    from typing import ParamSpec, TypeAlias
else:
    from typing_extensions import ParamSpec, TypeAlias

if TYPE_CHECKING:
    from aiofiles.base import AiofilesContextManager
    from aiofiles.threadpool.text import AsyncTextIOWrapper

    # noinspection PyUnresolvedReferences
    from configzen.model import ConfigModel
    from configzen.route import ConfigRoute

T = TypeVar("T")
P = ParamSpec("P")

ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
ConfigRouteLike: TypeAlias = Union[str, List[str], "ConfigRoute"]

if sys.version_info >= (3, 9) or TYPE_CHECKING:
    ConfigIO: TypeAlias = contextlib.AbstractContextManager[TextIO]
else:
    ConfigIO: TypeAlias = contextlib.AbstractContextManager

AsyncConfigIO: TypeAlias = "AiofilesContextManager[None, None, AsyncTextIOWrapper]"
RawResourceT: TypeAlias = Union[ConfigIO, str, int, os.PathLike, pathlib.Path]
NormalizedResourceT: TypeAlias = Union[ConfigIO, str, int, pathlib.Path]
IncludeExcludeT: TypeAlias = Optional[
    Union[
        AbstractSet[Union[int, str]],
        Mapping[Union[int, str], Any],
    ]
]
