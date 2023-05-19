import contextlib
import os
import pathlib
from typing import TYPE_CHECKING, TextIO, TypeAlias, TypeVar, Union

if TYPE_CHECKING:
    from aiofiles.base import AiofilesContextManager
    from aiofiles.threadpool.text import AsyncTextIOWrapper

    from configzen.config import Route

T = TypeVar("T")

ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
SupportsRoute: TypeAlias = Union[str, list[str], "Route"]

ConfigIO: TypeAlias = contextlib.AbstractContextManager[TextIO]
AsyncConfigIO: TypeAlias = "AiofilesContextManager[None, None, AsyncTextIOWrapper]"
RawResourceT: TypeAlias = Union[ConfigIO, str, int, os.PathLike, pathlib.Path]
