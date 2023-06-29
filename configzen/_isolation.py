from __future__ import annotations

import asyncio
import collections.abc
import contextvars
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from configzen.typedefs import T, P


def isolate_calls(
    func: collections.abc.Callable[P, T],
) -> collections.abc.Callable[P, T]:
    """
    Decorator to copy a function call context automatically (context isolation)
    to prevent collisions.

    This decorator will copy the current context and run the function
    in this new isolated context.
    """
    if asyncio.iscoroutinefunction(func):

        def wrapper(*args: P.args, **kwargs: P.kwargs) -> asyncio.Task[T]:
            return isolate_async(func, *args, **kwargs)

    else:

        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return isolate(func, *args, **kwargs)

    return wrapper


def isolate(
    func: collections.abc.Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Utility for running a function in an isolated context."""
    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)


def isolate_async(
    func: collections.abc.Callable[..., collections.abc.Coroutine[Any, Any, T]],
    *args: Any,
    **kwargs: Any,
) -> asyncio.Task[T]:
    """Utility for awaiting a coroutine in an isolated context."""
    return asyncio.create_task(func(*args, **kwargs))
