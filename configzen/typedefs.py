import contextlib
import os
import pathlib
import sys
from typing import TYPE_CHECKING, TextIO, TypeVar, Union

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    from typing_extensions import TypeAlias

if TYPE_CHECKING:
    from aiofiles.base import AiofilesContextManager
    from aiofiles.threadpool.text import AsyncTextIOWrapper

    from configzen.config import ConfigModel, Route

T = TypeVar("T")

ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
SupportsRoute: TypeAlias = Union[str, list[str], "Route"]

ConfigIO: TypeAlias = contextlib.AbstractContextManager[TextIO]
AsyncConfigIO: TypeAlias = "AiofilesContextManager[None, None, AsyncTextIOWrapper]"
RawResourceT: TypeAlias = Union[ConfigIO, str, int, os.PathLike, pathlib.Path]
