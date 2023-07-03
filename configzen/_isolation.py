from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable, Coroutine
from typing import Any, cast

from configzen.typedefs import P, T


def isolation_runner(
    func: Callable[P, T],
) -> Callable[P, T]:
    """
    Decorator to copy a function call context automatically (context isolation)
    to prevent collisions.

    This decorator will copy the current context and run the function
    in this new isolated context.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(isolation_runner(func.__func__))

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        def _isolating_async_wrapper(*args: Any, **kwargs: Any) -> asyncio.Task[T]:
            return isolate_await(
                cast(Callable[P, Coroutine[Any, Any, T]], func), *args, **kwargs
            )

        return cast(Callable[P, T], _isolating_async_wrapper)

    @functools.wraps(func)
    def _isolating_wrapper(*args: Any, **kwargs: Any) -> T:
        return isolate_run(func, *args, **kwargs)

    return _isolating_wrapper


def isolate_run(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Utility for running a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def isolate_await(
    func: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    **kwargs: Any,
) -> asyncio.Task[T]:
    """Utility for awaiting a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
