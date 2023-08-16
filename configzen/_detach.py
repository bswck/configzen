from __future__ import annotations

import asyncio
import contextvars
import functools
from typing import TYPE_CHECKING, cast, no_type_check

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from configzen.typedefs import P, T

# pyright: reportGeneralTypeIssues=false


@no_type_check
def detached_context_function(
    func: Callable[P, T],
) -> Callable[P, T]:
    """
    Copy a function call context automatically (context isolation)
    to prevent collisions.

    This decorator will copy the current context and run the function
    in this new isolated context.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(detached_context_function(func.__func__))

    if asyncio.iscoroutinefunction(func):

        @no_type_check
        @functools.wraps(func)
        def _detaching_async_wrapper(
            *args: object,
            **kwargs: object,
        ) -> asyncio.Task[T]:
            return detached_context_await(
                cast("Callable[P, Coroutine[object, object, T]]", func),
                *args,
                **kwargs,
            )

        return _detaching_async_wrapper

    @functools.wraps(func)
    def _detaching_wrapper(*args: object, **kwargs: object) -> T:
        return detached_context_run(func, *args, **kwargs)

    return _detaching_wrapper


def detached_context_run(
    func: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    """Run a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def detached_context_await(
    func: Callable[..., Coroutine[object, object, T]],
    *args: object,
    **kwargs: object,
) -> asyncio.Task[T]:
    """Await a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
