import collections.abc
import contextlib
import os
import pathlib
import sys
from typing import TYPE_CHECKING, Any, Optional, TextIO, TypeVar, Union

if sys.version_info >= (3, 10):
    from typing import ParamSpec, TypeAlias
else:
    from typing_extensions import ParamSpec, TypeAlias

if TYPE_CHECKING:
    from aiofiles.base import AiofilesContextManager
    from aiofiles.threadpool.text import AsyncTextIOWrapper

    from configzen.config import ConfigModel
    from configzen.route import ConfigRoute

T = TypeVar("T")
P = ParamSpec("P")

ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
ConfigRouteLike: TypeAlias = Union[str, list[str], "ConfigRoute"]

ConfigIO: TypeAlias = contextlib.AbstractContextManager[TextIO]
AsyncConfigIO: TypeAlias = "AiofilesContextManager[None, None, AsyncTextIOWrapper]"
RawResourceT: TypeAlias = Union[ConfigIO, str, int, os.PathLike, pathlib.Path]
NormalizedResourceT: TypeAlias = Union[ConfigIO, str, int, pathlib.Path]
IncludeExcludeT: TypeAlias = Optional[
    Union[
        collections.abc.Set[Union[int, str]],
        collections.abc.Mapping[Union[int, str], Any],
    ]
]
