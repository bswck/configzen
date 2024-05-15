"""Facilities for [contextual][contextvars] processing."""

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
    "isolated_context_function",
    "isolated_context_coroutine",
    "run_isolated",
    "async_run_isolated",
)


def isolated_context_function(func: Callable[_P, _T]) -> Callable[_P, _T]:
    """
    Copy the context automatically on function call.

    Allows to isolate the library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(isolated_context_function(func.__func__))

    @wraps(func)
    def copy(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return run_isolated(func, *args, **kwargs)

    return copy


def isolated_context_coroutine(
    func: Callable[_P, Coroutine[object, object, _T]],
) -> Callable[_P, Coroutine[object, object, _T]]:
    """
    Copy the context automatically on coroutine execution.

    Allows to isolate library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(isolated_context_coroutine(func.__func__))

    @wraps(func)
    async def copy_async(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        return await async_run_isolated(func, *args, **kwargs)

    return copy_async


def run_isolated(func: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Run a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def async_run_isolated(
    func: Callable[_P, Coroutine[object, object, _T]],
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> asyncio.Task[_T]:
    """Await a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
