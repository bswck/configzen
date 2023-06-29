import asyncio
import collections.abc
import contextvars
from typing import Any

from configzen.typedefs import T


def isolate_calls(
    func: collections.abc.Callable[..., T],
) -> collections.abc.Callable[..., T]:
    """
    Decorator to copy a function call context automatically (context isolation)
    to prevent collisions.

    This decorator will copy the current context and run the function
    in this new isolated context.
    """

    def wrapper(*args: Any, **kwargs: Any) -> T:
        # pylint: disable=protected-access
        return isolate_and_run(func, *args, **kwargs)

    return wrapper


def isolate_and_run(
    func: collections.abc.Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Utility for running a function in an isolated context."""
    if asyncio.iscoroutinefunction(func):
        return asyncio.create_task(func(*args, **kwargs))

    context = contextvars.copy_context()
    return context.run(func, *args, **kwargs)
