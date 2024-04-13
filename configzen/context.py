"""`configzen.context`: Isolate the library context from the user context."""

from __future__ import annotations

import asyncio
import contextvars
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import TypeVar

    from typing_extensions import ParamSpec

    _T = TypeVar("_T")
    _P = ParamSpec("_P")


__all__ = (
    "copy_context_on_call",
    "copy_context_on_await",
    "copy_and_run",
    "copy_and_await",
)


def copy_context_on_call(func: Callable[_P, _T]) -> Callable[_P, _T]:
    """
    Copy the context automatically on function call.

    Allows to isolate the library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(copy_context_on_call(func.__func__))

    @wraps(func)
    def copy(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return copy_and_run(func, *args, **kwargs)

    return copy


def copy_context_on_await(
    func: Callable[_P, Coroutine[object, object, _T]],
) -> Callable[_P, Coroutine[object, object, _T]]:
    """
    Copy the context automatically on coroutine execution.

    Allows to isolate library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(copy_context_on_await(func.__func__))

    @wraps(func)
    async def copy_async(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return await copy_and_await(func, *args, **kwargs)

    return copy_async


def copy_and_run(func: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Run a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def copy_and_await(
    func: Callable[_P, Coroutine[object, object, _T]],
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> asyncio.Task[_T]:
    """Await a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
