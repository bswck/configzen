from __future__ import annotations

import contextlib
import functools
from typing import TYPE_CHECKING, Any, cast, overload

from configzen.model import (
    ConfigModel,
    export_hook,
    export_model,
    export_model_async,
    field_hook,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

    from configzen.typedefs import ConfigModelT, T

# pyright: reportGeneralTypeIssues=false

__all__ = (
    "with_exporter",
    "with_async_exporter",
    "with_field_hook",
    "with_export_hook",
    "with_export_options",
)


@overload
def with_export_hook(
    func: Callable[[T], Any],
    cls: None = None,
) -> functools.partial[type[T]]:
    ...


@overload
def with_export_hook(
    func: Callable[[T], Any],
    cls: type[T],
) -> type[T]:
    ...


def with_export_hook(
    func: Callable[[T], Any],
    cls: type[T] | None = None,
) -> type[T] | functools.partial[type[T]]:
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
    ```python
    @with_export_hook(converter_func)
    class MyClass:
        ...
    ```
    """
    if cls is None:
        return functools.partial(with_export_hook, func)

    export_hook.register(cls, func)

    if not hasattr(cls, "__get_validators__"):

        def validator_gen() -> Iterator[Callable[[Any], Any]]:
            hook_func = field_hook.dispatch(cls)
            yield lambda value: hook_func(cls, value)

        with contextlib.suppress(TypeError):
            cls.__get_validators__ = validator_gen  # type: ignore[attr-defined]

    return cls


@overload
def with_field_hook(
    func: Callable[[type[T], Any], T],
    cls: type[T],
) -> type[T]:
    ...


@overload
def with_field_hook(
    func: Callable[[type[T], Any], T],
    cls: None = None,
) -> functools.partial[type[T]]:
    ...


def with_field_hook(
    func: Callable[[type[T], Any], T],
    cls: type[T] | None = None,
) -> type[T] | functools.partial[type[T]]:
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
    func: Callable[[ConfigModelT], Any] | None = None,
    cls: type[ConfigModelT] | None = None,
    **predefined_kwargs: Any,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a configuration model class.

    Parameters
    ----------
    func
        The exporter function.
    **predefined_kwargs
        The default keyword arguments to pass to the exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    if func and predefined_kwargs:
        msg = "specifying both a function and predefined kwargs is not supported"
        raise NotImplementedError(msg)

    if func is None:

        def func(obj: Any, **kwargs: Any) -> Any:
            kwargs.update(predefined_kwargs)
            return obj.export(**kwargs)

        export_model.register(cls, func)

        if export_model_async.dispatch(cls) is export_model_async:

            async def default_async_func(
                obj: Any,
                **kwargs: Any,
            ) -> Any:
                kwargs.update(predefined_kwargs)
                return await obj.export_async(**kwargs)

            export_model_async.register(cls, default_async_func)
    else:
        export_model.register(cls, func)
        if export_model_async.dispatch(cls) is export_model_async:

            async def default_async_func(
                obj: Any,
                **kwargs: Any,
            ) -> Any:
                nonlocal func
                if TYPE_CHECKING:
                    func = cast("Callable[..., dict[str, Any]]", func)

                return func(obj, **kwargs)

            export_model_async.register(cls, default_async_func)
    return cls


def with_async_exporter(
    func: Callable[[ConfigModelT], Coroutine[Any, Any, Any]] | None = None,
    cls: type[ConfigModelT] | None = None,
    **predefined_kwargs: Any,
) -> type[ConfigModelT] | Any:
    """
    Register a custom exporter for a configuration model class.

    Parameters
    ----------
    func
        The exporter function.
    **predefined_kwargs
        The default keyword arguments to pass to the exporter function.
    cls
        The type to register the exporter for.
    """
    if cls is None:
        return functools.partial(with_exporter, func)

    if func and predefined_kwargs:
        msg = "specifying both a function and default kwargs is not supported"
        raise NotImplementedError(msg)

    if func is None:

        async def default_async_func(obj: Any, **kwargs: Any) -> Any:
            kwargs.update(predefined_kwargs)
            return await obj.export_async(**kwargs)

        export_model_async.register(cls, default_async_func)
    else:
        export_model_async.register(cls, func)
    return cls


def _export_with_options(
    options: dict[str, Any],
    config_model: ConfigModel,
    **kwargs: Any,
) -> dict[str, Any]:
    return config_model.dict(**{**kwargs, **options})


async def _export_with_options_async(
    options: dict[str, Any],
    config_model: ConfigModel,
    **kwargs: Any,
) -> dict[str, Any]:
    return await config_model.dict_async(**{**kwargs, **options})


@overload
def with_export_options(
    cls: None = None,
    **options: Any,
) -> functools.partial[type[ConfigModelT]]:
    ...


@overload
def with_export_options(
    cls: type[ConfigModelT],
    **options: Any,
) -> type[ConfigModelT]:
    ...


def with_export_options(
    cls: type[ConfigModelT] | None = None,
    **options: Any,
) -> type[ConfigModelT] | functools.partial[type[ConfigModelT]]:
    """
    Register default options for a configuration model class dictionary exporter.

    Parameters
    ----------
    cls
        The configuration model class to register the options for.
    **options
        The default keyword arguments to pass to the exporter function.
    """
    if cls is None:
        return functools.partial(with_export_options, **options)
    export_model.register(cls, functools.partial(_export_with_options, options))
    export_model_async.register(
        cls,
        functools.partial(_export_with_options_async, options),
    )
    return cls
