from __future__ import annotations

import functools
import importlib
import inspect
import re
import string
import sys
from collections import ChainMap
from collections.abc import Callable, MutableMapping
from typing import TYPE_CHECKING, Any, ClassVar, cast, no_type_check

from pydantic.fields import Undefined, UndefinedType

from configzen.errors import ResourceLookupError

if TYPE_CHECKING:
    from configzen.typedefs import ConfigModelT

__all__ = (
    "interpolate",
    "include",
)

INTERPOLATOR: str = "__interpolator__"
EVALUATES: str = "__evaluates__"
EVALUATION_ENGINE: str = "__evaluation_engine__"
NAMESPACE_TOKEN: str = "::"
AT_TOKEN: str = "@"
OR_TOKEN: str = "||"
STRICT_TOKEN: str = "!"


class ConfigInterpolationTemplate(string.Template):
    delimiter = re.escape("$")
    idpattern = r"[^{}\$]+"
    # noinspection PyClassVar
    pattern: ClassVar[re.Pattern[str]] = rf"""
    {delimiter}(?:
      (?P<escaped>{delimiter})          | # Escape sequence of two delims
      (?P<named>{idpattern})            | # delimiter and a Python ident
      {{(?P<sbraced>{idpattern})}}      | # delimiter and a single-braced ident
      {{{{(?P<braced>{idpattern})}}}}   | # delimiter and a double-braced ident
      (?P<invalid>)                       # Other ill-formed delimiter expressions
    )
    """  # type: ignore[assignment]
    flags = re.VERBOSE | re.IGNORECASE

    def _invalid(self, mo: re.Match[str]) -> None:
        i = mo.start("invalid")
        lines = self.template[:i].splitlines(keepends=True)
        if not lines:
            colno = 1
            lineno = 1
        else:
            colno = i - len("".join(lines[:-1]))
            lineno = len(lines)
        msg = f"Invalid placeholder in string: line {lineno}, col {colno}"
        raise ValueError(msg)

    def interpolate(
        self,
        mapping: MutableMapping[str, Any] | None = None,
        /,
        **kwds: Any,
    ) -> str:
        if mapping is None:
            mapping = kwds
        elif kwds:
            mapping = ChainMap(kwds, mapping)

        # Helper function for .sub()
        def convert(mo: re.Match[str]) -> str:
            named = mo.group("named") or mo.group("braced") or mo.group("sbraced")
            if named is not None:
                try:
                    return str(mapping[named])
                except KeyError:
                    return mo.group()
            if mo.group("escaped") is not None:
                return self.delimiter
            if mo.group("invalid") is not None:
                return mo.group()
            msg = "Unrecognized named group in pattern"
            raise ValueError(msg, self.pattern)

        return self.pattern.sub(convert, self.template)

    if sys.version_info < (3, 11):

        def get_identifiers(self) -> list[str]:
            ids = []
            for mo in self.pattern.finditer(self.template):
                named = mo.group("named") or mo.group("braced") or mo.group("sbraced")
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
                    msg = "Unrecognized named group in pattern"
                    raise ValueError(msg, self.pattern)
            return ids


@functools.singledispatch
def interpolate(
    value: object,
    _cls: type[ConfigModelT],
    _closest_ns: dict[str, Any],
    _target_type: type[Any],
) -> Any:
    """
    Interpolate a value.

    Usually applies to strings, but can be extended to other types.
    """
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
        expressions=identifiers,
        closest_namespace=closest_namespace,
        target_type=target_type,
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
        template=template,
        namespace=namespace,
        target_type=target_type,
    )


def _ensure_dispatchable_type(
    target_type: type[Any],
) -> type[Any]:
    if not getattr(target_type, "__mro__", None):
        target_type = object
    return target_type


class BaseInterpolator:
    def interpolate_many(
        self,
        template: ConfigInterpolationTemplate,
        namespace: dict[str, Any],
        target_type: type[Any],
    ) -> Any:
        dispatch_type = _ensure_dispatchable_type(target_type)
        bulk_render = self.bulk_renderers.dispatch(dispatch_type)
        return bulk_render(self, template, namespace)

    # noinspection PyMethodMayBeStatic
    def bulk_render_any(
        self,
        template: ConfigInterpolationTemplate,
        namespace: dict[str, Any],
    ) -> Any:
        return template.interpolate(namespace)

    def interpolate_one(
        self,
        template: ConfigInterpolationTemplate,
        identifier: str,
        value: Any,
        target_type: type[Any],
    ) -> Any:
        dispatch_type = _ensure_dispatchable_type(target_type)
        render = self.single_renderers.dispatch(dispatch_type)
        return render(self, template, identifier, value)

    # noinspection PyMethodMayBeStatic
    def single_render_any(
        self,
        _template: ConfigInterpolationTemplate,
        _identifier: str,
        value: Any,
    ) -> Any:
        return value

    bulk_renderers = functools.singledispatch(bulk_render_any)
    single_renderers = functools.singledispatch(single_render_any)


def evaluates(
    evaluator_name: str,
) -> Callable[[Callable[[Any], Any]], Callable[[Any], Any]]:
    def decorator(evaluator: Callable[[Any], Any]) -> Callable[[Any], Any]:
        setattr(evaluator, EVALUATES, evaluator_name.casefold())
        return evaluator

    return decorator


class BaseEvaluationEngine:
    evaluators_by_name: ClassVar[dict[str, Any]] = {}

    def evaluate_expression_impl(
        self,
        expression: str,
        result_namespace: dict[str, Any],
        namespaces: dict[str | None, dict[str, Any]],
        closest_namespace: dict[str, Any],
        target_type: type[Any],
    ) -> Any:
        from configzen import field_hook

        result = Undefined
        final_result = Undefined
        for identifier in filter(
            None,
            map(str.strip, expression.strip().split(OR_TOKEN)),
        ):
            result = self.resolve_identifier(
                identifier=identifier,
                result_namespace=result_namespace,
                namespaces=namespaces,
                closest_namespace=closest_namespace,
            )

            if result is not Undefined:
                result = field_hook(target_type, result)
                result_namespace[identifier] = result

            if result:
                final_result = result
                break

        if final_result is not Undefined:
            result_namespace[expression] = final_result
        return result

    @staticmethod
    def resolve_identifier(
        identifier: str,
        result_namespace: dict[str, Any],
        namespaces: dict[str | None, dict[str, Any]],
        closest_namespace: dict[str, Any],
        strict: bool | None = None,
    ) -> Any:
        from configzen.model import at

        if (identifier.startswith('"') and identifier.endswith('"')) or (
            identifier.startswith("'") and identifier.endswith("'")
        ):
            return identifier[1:-1]

        if strict is None:
            strict = identifier.startswith("!")
            if strict:
                identifier = identifier[1:].strip()

        ns_name, uses_ns, ident = identifier.rpartition(NAMESPACE_TOKEN)
        ns_at_ident = ""
        global_namespace = namespaces[None]

        if uses_ns:
            # [namespace[[.member]*@ns_at_ident[.member]*]]::identifier
            namespaces_resolution_order = [
                global_namespace,
                closest_namespace,
                result_namespace,
            ]
            if ns_name and uses_ns:
                ns_name, _, ns_at_ident = ns_name.rpartition(AT_TOKEN)
                if ns_name in namespaces:
                    namespaces_resolution_order.insert(0, namespaces[ns_name])
        else:
            # identifier[.member]*
            namespaces_resolution_order = [
                closest_namespace,
                result_namespace,
                global_namespace,
            ]

        ident, _, ident_at = ident.partition(AT_TOKEN)

        lookup_key = ns_name if uses_ns and ns_name else ident
        lookup_value: dict[str, Any] | UndefinedType = Undefined

        try:
            for namespace in namespaces_resolution_order:
                lookup_value = at(namespace, lookup_key)
                if lookup_value is not Undefined:
                    break
        except ResourceLookupError:
            if strict:
                raise

        if lookup_value is Undefined:
            return identifier

        value = lookup_value

        if uses_ns and ns_name:
            namespace = cast("dict[str, Any]", value)

            if ns_at_ident:
                namespace = at(namespace, ns_at_ident)

            try:
                value = at(namespace, ident)
            except ResourceLookupError:
                if strict:
                    raise

            if value is Undefined:
                return Undefined

        if ident_at:
            value = at(value, ident_at)

        result_namespace[identifier] = value
        return value

    evaluators_by_class = functools.singledispatch(evaluate_expression_impl)

    def evaluate_expression(
        self,
        expression: str,
        result_namespace: dict[str, Any],
        namespaces: dict[str | None, dict[str, Any]],
        closest_namespace: dict[str, Any],
        target_type: type[Any],
    ) -> Any:
        dispatch_type = _ensure_dispatchable_type(target_type)
        evaluator = self.evaluators_by_class.dispatch(dispatch_type)
        return evaluator(
            self,
            expression,
            result_namespace,
            namespaces,
            closest_namespace,
            target_type,
        )

    def __init_subclass__(cls, *, register_evaluators: bool = True) -> None:
        super().__init_subclass__()
        if cls.evaluators_by_name is None:
            cls.evaluators_by_name = {}

        if register_evaluators:
            # Suggestion: Replace with `inspect.getmembers_static()` by 2026-10.
            for _, func in inspect.getmembers(cls, inspect.isfunction):
                name = getattr(func, EVALUATES, None)
                if name is not None:
                    cls.register_evaluator(name, func)

    @classmethod
    def register_evaluator(cls, name: str, evaluator: Callable[[Any], Any]) -> None:
        cls.evaluators_by_name[name] = evaluator


BaseEvaluationEngine.__init_subclass__()


def _include_wrapper(
    cls: type[ConfigModelT],
    namespace_name: str | None,
    namespace_factory: Callable[[], dict[str, Any]],
) -> type[ConfigModelT]:
    from configzen.model import INTERPOLATION_INCLUSIONS

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
    msg = f"Cannot include {namespace} (unexpected type {type(namespace).__name__})"
    raise TypeError(msg)


@no_type_check
@include.register(dict)
def include_const(
    namespace: dict[str, Any] | ConfigModelT,
    /,
    *,
    name: str | None = None,
) -> Callable[[type[ConfigModelT]], type[ConfigModelT]]:
    from configzen import ConfigModel

    if isinstance(namespace, ConfigModel):
        return lambda cls: _include_wrapper(
            cls,
            name,
            lambda: namespace.dict(),
        )
    return lambda cls: _include_wrapper(cls, name, lambda: namespace)


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
            msg = f"Namespace {namespace!r} not found in {callers_globals['__name__']}"
            raise NameError(msg) from None
        if isinstance(namespace_variable, dict):
            return namespace_variable
        if isinstance(namespace_variable, ConfigModel):
            return namespace_variable.dict()
        msg = (
            f"Cannot include {namespace!r}"
            f"(unexpected type {type(namespace_variable).__name__})"
        )
        raise TypeError(msg)

    return lambda cls: _include_wrapper(cls, name, namespace_factory)
