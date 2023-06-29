from __future__ import annotations

import collections.abc
import contextlib
import functools
from typing import TYPE_CHECKING, Any, cast

from configzen.config import export_hook, export_model, export_model_async, field_hook

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT, T

__all__ = (
    "with_exporter",
    "with_async_exporter",
    "with_field_hook",
    "with_export_hook",
)


def with_export_hook(
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

        @with_export_hook(converter_func)
        class MyClass:
            ...

    """
    if cls is None:
        return functools.partial(with_export_hook, func)

    export_hook.register(cls, func)

    if not hasattr(cls, "__get_validators__"):

        def validator_gen() -> (
            collections.abc.Iterator[collections.abc.Callable[[Any], Any]]
        ):
            hook_func = field_hook.dispatch(cls)  # type: ignore[arg-type]
            yield lambda value: hook_func(cls, value)

        with contextlib.suppress(TypeError):
            cls.__get_validators__ = validator_gen  # type: ignore[attr-defined]

    return cls


def with_field_hook(
    func: collections.abc.Callable[[type[T], Any], T], cls: type[T] | None = None
) -> type[T] | Any:
    """
    Register a field hook for a type.

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
        return functools.partial(with_field_hook, func)

    field_hook.register(cls, func)
    return cls


def with_exporter(
    func: collections.abc.Callable[[ConfigModelT], dict[str, Any]] | None = None,
    cls: type[ConfigModelT] | None = None,
    **predefined_kwargs: Any,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a configuration model class.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    if func and predefined_kwargs:
        raise NotImplementedError(
            "specifying both a function and predefined kwargs is not supported"
        )

    if func is None:

        def func(obj: Any, **kwargs: Any) -> Any:
            kwargs |= predefined_kwargs
            return obj.export(**kwargs)

        export_model.register(cls, func)

        if export_model_async.dispatch(cls) is export_model_async:

            async def default_async_func(obj: Any, **kwargs: Any) -> Any:
                kwargs |= predefined_kwargs
                return await obj.export_async(**kwargs)

            export_model_async.register(cls, default_async_func)
    else:
        export_model.register(cls, func)
        if export_model_async.dispatch(cls) is export_model_async:

            async def default_async_func(obj: Any, **kwargs: Any) -> Any:
                nonlocal func
                if TYPE_CHECKING:
                    func = cast(collections.abc.Callable[..., dict[str, Any]], func)

                return func(obj, **kwargs)

            export_model_async.register(cls, default_async_func)
    return cls


def with_async_exporter(
    func: collections.abc.Callable[
        [ConfigModelT], collections.abc.Coroutine[Any, Any, dict[str, Any]]
    ]
    | None = None,
    cls: type[ConfigModelT] | None = None,
    **predefined_kwargs: Any,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a configuration model class.

    Parameters
    ----------
    func
        The exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    if func and predefined_kwargs:
        raise NotImplementedError(
            "specifying both a function and default kwargs is not supported"
        )

    if func is None:

        async def default_async_func(obj: Any, **kwargs: Any) -> Any:
            kwargs |= predefined_kwargs
            return await obj.export_async(**kwargs)

        export_model_async.register(cls, default_async_func)
    else:
        export_model_async.register(cls, func)
    return cls
