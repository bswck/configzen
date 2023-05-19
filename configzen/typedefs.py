import contextlib
import os
import pathlib
from typing import TypeVar, TypeAlias, Union, TextIO, TYPE_CHECKING


if TYPE_CHECKING:
    from aiofiles.threadpool.text import AsyncTextIOWrapper
    from aiofiles.base import AiofilesContextManager
    from configzen.config import Route

T = TypeVar("T")

ConfigModelT = TypeVar("ConfigModelT", bound="ConfigModel")
SupportsRoute: TypeAlias = Union[str, list[str], "Route"]

ConfigIO: TypeAlias = contextlib.AbstractContextManager[TextIO]
AsyncConfigIO: TypeAlias = "AiofilesContextManager[None, None, AsyncTextIOWrapper]"
RawResourceT: TypeAlias = Union[ConfigIO, str, os.PathLike, pathlib.Path]
