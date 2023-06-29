from __future__ import annotations

import asyncio
import collections.abc
import contextvars
from typing import Any, cast, TypeVar

from configzen.typedefs import T


_IsolatedFuncT = TypeVar("_IsolatedFuncT")


def isolate_calls(
    func: _IsolatedFuncT
) -> _IsolatedFuncT:
    """
    Decorator to copy a function call context automatically (context isolation)
    to prevent collisions.

    This decorator will copy the current context and run the function
    in this new isolated context.
    """
    if asyncio.iscoroutinefunction(func):
        return cast(
            _IsolatedFuncT,
            lambda *args, **kwargs: isolate_async(func, *args, **kwargs)
        )
    return cast(
        _IsolatedFuncT,
        lambda *args, **kwargs: isolate(func, *args, **kwargs)  # type: ignore[arg-type]
    )


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
