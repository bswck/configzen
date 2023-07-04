from __future__ import annotations

import functools
import importlib
import inspect
import re
import string
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT

__all__ = (
    "interpolate",
    "include",
)

INTERPOLATOR: str = "__interpolator__"
NAMESPACE_TOKEN: str = "::"
AT_TOKEN: str = "@"


class ConfigInterpolationTemplate(string.Template):
    idpattern = rf"([_a-z]*({NAMESPACE_TOKEN}|{AT_TOKEN})?[_a-z][\\\.\[\]_a-z0-9]*)"
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
    value: Any,
    _cls: type[ConfigModelT],
    _closest_ns: dict[str, Any],
    _target_type: type[Any],
) -> Any:
    return value


@interpolate.register(str)
def _try_interpolate_str(
    value: str,
    cls: type[ConfigModelT],
    closest_namespace: dict[str, Any],
    target_type: type[Any],
) -> Any:
    template = ConfigInterpolationTemplate(value)
    identifiers = set(template.get_identifiers())
    if not identifiers:
        return value

    namespace = cls.get_interpolation_namespace(
        identifiers=identifiers, closest_namespace=closest_namespace
    )
    interpolator: BaseInterpolator = getattr(cls, INTERPOLATOR)

    if len(identifiers) == 1:
        identifier = identifiers.pop()
        value = namespace[identifier]
        return interpolator.interpolate_one(
            template=template,
            identifier=identifier,
            value=value,
            target_type=target_type,
        )

    return interpolator.interpolate_many(
        template=template, namespace=namespace, target_type=target_type
    )


class BaseInterpolator:
    def interpolate_many(
        self,
        template: ConfigInterpolationTemplate,
        namespace: dict[str, Any],
        target_type: type[Any],
    ) -> Any:
        bulk_render = self.bulk_renderers.dispatch(target_type)
        return bulk_render(self, template, namespace)

    # noinspection PyMethodMayBeStatic
    def bulk_render_any(
        self, template: ConfigInterpolationTemplate, namespace: dict[str, Any]
    ) -> Any:
        return template.safe_substitute(namespace)

    def interpolate_one(
        self,
        template: ConfigInterpolationTemplate,
        identifier: str,
        value: Any,
        target_type: type[Any],
    ) -> Any:
        render = self.single_renderers.dispatch(target_type)
        return render(self, template, identifier, value)

    # noinspection PyMethodMayBeStatic
    def single_render_any(
        self, _template: ConfigInterpolationTemplate, _identifier: str, value: Any
    ) -> Any:
        return value

    bulk_renderers = functools.singledispatch(bulk_render_any)
    single_renderers = functools.singledispatch(single_render_any)


def _include_wrapper(
    cls: type[ConfigModelT],
    namespace_name: str | None,
    namespace_factory: Callable[[], dict[str, Any]],
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
) -> Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    raise TypeError(
        f"Cannot include {namespace} (unexpected type {type(namespace).__name__})"
    )


@include.register(dict)
def include_const(
    namespace: dict[str, Any] | ConfigModelT, /, *, name: str | None = None
) -> Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    from configzen import ConfigModel

    if isinstance(namespace, ConfigModel):
        return lambda cls: _include_wrapper(
            cls, name, lambda: namespace.dict()  # type: ignore
        )
    return lambda cls: _include_wrapper(cls, name, lambda: namespace)  # type: ignore


def _include_factory(
    namespace_factory: Callable[[], dict[str, Any]],
    /,
    *,
    name: str | None = None,
) -> Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    return lambda cls: _include_wrapper(cls, name, namespace_factory)


if not TYPE_CHECKING:
    include.register(Callable, _include_factory)


@include.register(str)
def _include_str(
    namespace: str,
    /,
    *,
    name: str | None = None,
    stack_offset: int = 2,
    module: str | None = None,
    isolate_from_toplevel: bool = True,
) -> Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    if module is None:
        callers_globals = inspect.stack()[stack_offset].frame.f_globals
    else:
        callers_globals = None

    if isolate_from_toplevel and name is None:
        name = namespace

    def namespace_factory() -> dict[str, Any]:
        nonlocal callers_globals
        from configzen import ConfigModel

        if callers_globals is None:
            assert module
            module_obj = importlib.import_module(module)
            callers_globals = module_obj.__dict__

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
