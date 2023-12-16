"""`configzen.copy_context`: Isolate the library context from the user context."""
from __future__ import annotations

import asyncio
import contextvars
import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import TypeVar

    from typing_extensions import ParamSpec

    T = TypeVar("T")
    P = ParamSpec("P")


__all__ = (
    "copy_context_on_call",
    "copy_context_on_await",
    "copy_and_run",
    "copy_and_await",
)


def copy_context_on_call(func: Callable[P, T]) -> Callable[P, T]:
    """
    Copy the context automatically on function call.

    Allows to isolate the library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(copy_context_on_call(func.__func__))

    @functools.wraps(func)
    def copy(*args: object, **kwargs: object) -> T:
        return copy_and_run(func, *args, **kwargs)

    return copy


def copy_context_on_await(
    func: Callable[P, Coroutine[object, object, T]],
) -> Callable[P, Coroutine[object, object, T]]:
    """
    Copy the context automatically on coroutine execution.

    Allows to isolate library context from the user context.

    Used as a decorator.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(copy_context_on_await(func.__func__))

    @functools.wraps(func)
    async def copy_async(*args: object, **kwargs: object) -> T:
        return await copy_and_await(func, *args, **kwargs)

    return copy_async


def copy_and_run(func: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def copy_and_await(
    func: Callable[..., Coroutine[object, object, T]],
    *args: object,
    **kwargs: object,
) -> asyncio.Task[T]:
    """Await a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
