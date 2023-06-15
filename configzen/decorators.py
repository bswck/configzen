from __future__ import annotations

import collections.abc
import functools
from typing import TYPE_CHECKING, Any

from configzen.config import export, export_async, post_deserialize, pre_serialize

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT, T

__all__ = (
    "with_exporter",
    "with_async_exporter",
    "with_post_deserialize",
    "with_pre_serialize",
)


def with_pre_serialize(
    func: collections.abc.Callable[[T], Any], cls: type[T] | None = None
) -> type[T] | Any:
    """
    Register a pre-serialization converter function for a type.

    Parameters
    ----------
    func
        The converter function.

    cls
        The type to register the converter for.
        Optional for the decoration syntax.

    Returns
    -------
    The conversion result class.

    Usage
    -----
    .. code-block:: python

        @with_pre_serialize(converter_func)
        class MyClass:
            ...

    """
    if cls is None:
        return functools.partial(with_pre_serialize, func)

    pre_serialize.register(cls, func)

    if not hasattr(cls, "__get_validators__"):

        def validator_gen() -> (
            collections.abc.Iterator[collections.abc.Callable[[Any], Any]]
        ):
            yield lambda value: post_deserialize.dispatch(cls)(cls, value)

        cls.__get_validators__ = validator_gen  # type: ignore[attr-defined]

    return cls


def with_post_deserialize(
    func: collections.abc.Callable[[Any], T], cls: type[T] | None = None
) -> type[T] | Any:
    """
    Register a loader function for a type.

    Parameters
    ----------
    func
        The loader function.
    cls
        The type to register the loader for.

    Returns
    -------
    The loading result class.
    """

    if cls is None:
        return functools.partial(with_post_deserialize, func)

    post_deserialize.register(cls, func)
    return cls


def with_exporter(
    func: collections.abc.Callable[[ConfigModelT], dict[str, Any]],
    cls: type[ConfigModelT] | None = None,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a type.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    export.register(cls, func)
    if export_async.dispatch(cls) is export_async:

        async def default_async_func(obj: Any) -> Any:
            return func(obj)

        export_async.register(cls, default_async_func)
    return cls


def with_async_exporter(
    func: collections.abc.Callable[
        [ConfigModelT], collections.abc.Coroutine[Any, Any, dict[str, Any]]
    ],
    cls: type[ConfigModelT] | None = None,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a type.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    export_async.register(cls, func)
    return cls
