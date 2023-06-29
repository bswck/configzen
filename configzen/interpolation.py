from __future__ import annotations

import collections.abc
import functools
import inspect
import re
import string
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT

__all__ = (
    "interpolate",
    "include",
)

INTERPOLATION_NAMESPACE_TOKEN: str = "::"


class ConfigInterpolationTemplate(string.Template):
    idpattern = rf"(({INTERPOLATION_NAMESPACE_TOKEN})?[_a-z][\\\.\[\]_a-z0-9]*)"
    flags = re.IGNORECASE

    if sys.version_info < (3, 11):

        def get_identifiers(self) -> list[str]:
            ids = []
            for mo in self.pattern.finditer(self.template):
                named = mo.group("named") or mo.group("braced")
                if named is not None and named not in ids:
                    # add a named group only the first time it appears
                    ids.append(named)
                elif (
                    named is None
                    and mo.group("invalid") is None
                    and mo.group("escaped") is None
                ):
                    # If all the groups are None, there must be
                    # another group we"re not expecting
                    raise ValueError(
                        "Unrecognized named group in pattern", self.pattern
                    )
            return ids


@functools.singledispatch
def interpolate(
    value: Any, _cls: type[ConfigModelT], _closest_ns: dict[str, Any]
) -> Any:
    return value


@interpolate.register(str)
def _interpolate_str(
    value: str, cls: type[ConfigModelT], closest_namespace: dict[str, Any]
) -> str:
    template = ConfigInterpolationTemplate(value)
    value, namespace = cls.get_interpolation_namespace(
        identifiers=set(template.get_identifiers()), closest_namespace=closest_namespace
    )
    if value is None:
        return template.safe_substitute(namespace)
    return value


# todo(bswck): Interpolation for compound types. Quite non-trivial.


def _include_wrapper(
    cls: type[ConfigModelT],
    namespace_name: str | None,
    namespace_factory: collections.abc.Callable[[], dict[str, Any]],
) -> type[ConfigModelT]:
    from configzen.config import INTERPOLATION_INCLUSIONS

    getattr(cls, INTERPOLATION_INCLUSIONS)[namespace_name] = namespace_factory
    return cls


# noinspection PyUnusedLocal
@functools.singledispatch
def include(
    namespace: Any,
    /,
    *,
    name: str | None = None,  # noqa: ARG001
    **kwargs: Any,  # noqa: ARG001
) -> collections.abc.Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    raise TypeError(
        f"Cannot include {namespace} (unexpected type {type(namespace).__name__})"
    )


@include.register(dict)
def include_const(
    namespace: dict[str, Any] | ConfigModelT,
    /,
    *,
    name: str | None = None
) -> collections.abc.Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    from configzen import ConfigModel

    if isinstance(namespace, ConfigModel):
        return lambda cls: _include_wrapper(
            cls, name, lambda: namespace.dict()  # type: ignore
        )
    return lambda cls: _include_wrapper(cls, name, lambda: namespace)  # type: ignore


def _include_factory(
    namespace_factory: collections.abc.Callable[[], dict[str, Any]],
    /,
    *,
    name: str | None = None,
) -> collections.abc.Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    return lambda cls: _include_wrapper(cls, name, namespace_factory)


if not TYPE_CHECKING:
    include.register(collections.abc.Callable, _include_factory)


@include.register(str)
def _include_str(
    namespace: str,
    /,
    *,
    name: str | None = None,
    stack_offset: int = 2,
    isolate_from_toplevel: bool = False,
) -> collections.abc.Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    callers_globals = inspect.stack()[stack_offset].frame.f_globals

    if isolate_from_toplevel and name is None:
        name = namespace

    def namespace_factory() -> dict[str, Any]:
        from configzen import ConfigModel

        try:
            namespace_variable = callers_globals[namespace]
        except KeyError:
            raise NameError(
                f"Namespace {namespace!r} not found in {callers_globals['__name__']}"
            ) from None
        if isinstance(namespace_variable, dict):
            return namespace_variable
        if isinstance(namespace_variable, ConfigModel):
            return namespace_variable.dict()
        raise TypeError(
            f"Cannot include {namespace!r} (unexpected type "
            f"{type(namespace_variable).__name__})"
        )

    return lambda cls: _include_wrapper(cls, name, namespace_factory)
