from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import pathlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypedDict, TypeVar, cast

from anyconfig.utils import is_dict_like, is_list_like

from configzen.errors import (
    ConfigPreprocessingError,
    InternalConfigError,
    pretty_syntax_error,
)
from configzen.typedefs import ConfigModelT, SupportsRoute

if TYPE_CHECKING:
    from configzen.config import BaseContext, ConfigManager

__all__ = (
    "DirectiveContext",
    "directive",
    "Processor",
)


DirectiveT = TypeVar("DirectiveT")

EXPORT: str = "__configzen_export__"
EXECUTES_DIRECTIVES: str = "__configzen_executes_directives__"
EXECUTES_DIRECTIVES_ASYNC: str = "__configzen_executes_directives_async__"


def directive(
    name: str | enum.Enum,
    *,
    asynchronous: bool | None = None,
) -> Callable[..., Any]:
    """
    Decorator for creating processor directives.

    Parameters
    ----------
    name
        The name of the directive.
    asynchronous
        Whether the decorated directive function is asynchronous.

    Returns
    -------
    The decorated function.
    """
    if isinstance(name, enum.Enum):
        name = name.value.casefold()

    def decorator(func: Any) -> Any:
        nonlocal asynchronous
        if asynchronous is None:
            asynchronous = asyncio.iscoroutinefunction(func)
        attr = EXECUTES_DIRECTIVES_ASYNC if asynchronous else EXECUTES_DIRECTIVES
        if not hasattr(func, attr):
            setattr(func, attr, set())
        getattr(func, attr).add(name)
        return func

    return decorator


@dataclasses.dataclass
class DirectiveContext:
    """
    Context for processor directives.

    Attributes
    ----------
    directive
        The directive.
    key
        The key of the directive.
    prefix
        The prefix of the directive.
    arguments
        The arguments of the directive.
    snippet
        The config snippet where this directive was invoked.
    container
        The dictionary that contains the :attr:`dict`.

    """

    directive: str
    key: str
    prefix: str
    arguments: list[str]
    snippet: dict[str, Any]
    container: dict[str, Any]

    def has_duplicates(self, *, require_same_arguments: bool = True) -> bool:
        """
        Return whether the directive has duplicates.

        Returns
        -------
        Whether the directive has duplicates.
        """
        for key in self.container:
            directive_name, arguments = parse_directive_call(self.prefix, key)
            if directive_name == self.directive:
                if require_same_arguments and arguments != self.arguments:
                    continue
                return True
        return False


class Tokens(str, enum.Enum):
    LPAREN: str = "("
    RPAREN: str = ")"
    COMMAS: str = ",;"
    STRING: str = "\"'"
    ESCAPE: str = "\\"


class ArgumentSyntaxError(ValueError):
    """
    Raised when there is a syntax error in an argument.
    """


def _parse_argument_string_impl(  # noqa: C901, PLR0912, PLR0915
    raw_argument_string: str,
    tokens: type[Tokens] = Tokens,
) -> list[str]:
    prev_char = None
    string_ctx = None
    escape_ctx = False
    arguments: list[str] = []
    argument = ""
    emit = arguments.append
    explicit_strings_ctx = True

    tok_escape = tokens.ESCAPE
    tok_string = tokens.STRING
    tok_commas = tokens.COMMAS

    for char_no, char in enumerate(raw_argument_string, start=1):
        if escape_ctx:
            escape_ctx = False
            argument += char
        elif char in tok_escape:
            escape_ctx = True
        elif char in tok_string:
            if string_ctx and not explicit_strings_ctx:
                msg = f"Implicit string closed with explicit string character {char}"
                raise InternalConfigError(msg, extra=char_no)
            explicit_strings_ctx = True
            if string_ctx is None:
                # we enter a string
                string_ctx = char
            elif string_ctx == char:
                # we exit a string
                string_ctx = None
            else:
                # we are in a string
                argument += char
        elif char in tok_commas:
            if string_ctx:
                if not explicit_strings_ctx:
                    string_ctx = None
                    explicit_strings_ctx = True
                    emit(argument)
                    argument = ""
            else:
                if prev_char in {*tok_commas, None}:
                    msg = "Empty argument"
                    raise InternalConfigError(msg, extra=char_no)
                emit(argument)
                argument = ""
                explicit_strings_ctx = False
        elif not string_ctx and not char.isspace():
            if prev_char in {*tok_commas, None}:
                string_ctx = char
                argument += char
                explicit_strings_ctx = False
            if explicit_strings_ctx:
                msg = "Unexpected character after explicit string"
                raise InternalConfigError(msg, extra=char_no)
        else:
            argument += char
        prev_char = char
    return arguments


def _parse_argument_string(
    raw_argument_string: str,
    tokens: type[Tokens] = Tokens,
) -> list[str]:
    """Half for jokes, half for serious use"""

    tok_commas = tokens.COMMAS

    if any(raw_argument_string.endswith(tok) for tok in tok_commas):
        raw_argument_string = raw_argument_string[:-1]
    raw_argument_string += tok_commas[0]

    if raw_argument_string in tok_commas:
        return []

    with pretty_syntax_error(raw_argument_string):
        return _parse_argument_string_impl(raw_argument_string, tokens)


def parse_directive_call(
    prefix: str,
    directive_name: str,
    tokens: type[Tokens] = Tokens,
) -> tuple[str, list[str]]:
    arguments = []
    if directive_name.startswith(prefix):
        directive_name = directive_name[len(prefix) :].casefold()

        if directive_name.endswith(tokens.RPAREN):
            try:
                lpar = directive_name.index(tokens.LPAREN)
            except ValueError:
                msg = f"invalid directive call: {directive_name}"
                raise ConfigPreprocessingError(msg) from None
            (directive_name, raw_argument_string) = (
                directive_name[:lpar],
                directive_name[lpar + 1 : -1],
            )
            arguments = _parse_argument_string(raw_argument_string, tokens)

        if not directive_name.isidentifier():
            msg = f"invalid directive name: {directive_name}"
            raise ConfigPreprocessingError(msg)

    return directive_name, arguments


if TYPE_CHECKING:

    class ExportMetadata(TypedDict, Generic[ConfigModelT]):
        route: str | None
        context: BaseContext[ConfigModelT]
        key_order: list[str]
        preprocess: bool

else:

    class ExportMetadata(TypedDict):
        """
        Metadata for exporting.

        Attributes
        ----------
        route
            The route to import from.
        context
            The context attached to the import.
        """

        route: str | None
        context: BaseContext[ConfigModelT]
        key_order: list[str]
        preprocess: bool


class BaseProcessor(Generic[ConfigModelT]):
    """
    Processor that executes directives.

    Attributes
    ----------
    dict_config
        The dictionary config to parse and update.
    directive_prefix
        The prefix for directives.
    """

    _directive_handlers: dict[str, Any] = None  # type: ignore[assignment]
    _async_directive_handlers: dict[str, Any] = None  # type: ignore[assignment]
    directive_prefix: ClassVar[str]
    extension_prefix: ClassVar[str]

    def __init__(
        self,
        manager: ConfigManager[ConfigModelT],
        dict_config: dict[str, Any],
    ) -> None:
        self.manager = manager
        self.dict_config = dict_config

    @classmethod
    def export(
        cls,
        state: Any,
        *,
        metadata: ExportMetadata[ConfigModelT] | None = None,
    ) -> None:
        """
        Export the state.

        Parameters
        ----------
        state
            The state to export.
        metadata
            The metadata of the substitution.
        """
        if is_dict_like(state):
            if metadata is None:
                from configzen.config import CONTEXT

                state.pop(CONTEXT, None)
                metadata = state.pop(EXPORT, None)
            if metadata:
                cls._export(state, metadata)
            else:
                cls.export(list(state.values()), metadata=None)
        elif is_list_like(state):
            for item in state:
                cls.export(item, metadata=None)

    @classmethod
    async def export_async(
        cls,
        state: Any,
        *,
        metadata: ExportMetadata[ConfigModelT] | None = None,
    ) -> None:
        """
        Export the state asynchronously.

        Parameters
        ----------
        state
            The state to export.
        metadata
            The metadata of the substitution.
        """
        if is_dict_like(state):
            if metadata is None:
                from configzen.config import CONTEXT

                state.pop(CONTEXT, None)
                metadata = state.pop(EXPORT, None)
            if metadata:
                await cls._export_async(state, metadata)
            else:
                await cls.export_async(list(state.values()), metadata=None)
        elif is_list_like(state):
            for item in state:
                await cls.export_async(item, metadata=None)

    @classmethod
    def _export(
        cls,
        state: Any,
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        raise NotImplementedError

    @classmethod
    async def _export_async(
        cls,
        state: Any,
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        raise NotImplementedError

    async def preprocess_async(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config,
        ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return cast(dict[str, Any], await self._preprocess_async(self.dict_config))

    def preprocess(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config,
        ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return cast(dict[str, Any], self._preprocess(self.dict_config))

    def _preprocess(self, container: Any) -> Any:
        if not is_dict_like(container):
            if is_list_like(container):
                return [self._preprocess(v) for v in container]
            return container

        result: dict[str, Any] = {}

        for key, value in sorted(
            cast(dict[str, Any], container).items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                actual_key = key.lstrip(self.extension_prefix)
                overridden = result.get(actual_key, {})
                if not is_dict_like(overridden):
                    raise ConfigPreprocessingError(
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {actual_key!r} "
                        f"is not a dictionary"
                    )
                replacement = overridden | value
                result[actual_key] = self._preprocess(replacement)
            elif key.startswith(self.directive_prefix):
                directive_name, arguments = parse_directive_call(
                    self.directive_prefix, key
                )
                context_container = container.copy()
                del context_container[key]
                context = DirectiveContext(
                    directive=directive_name,
                    key=key,
                    prefix=self.directive_prefix,
                    arguments=arguments,
                    snippet=value,
                    container=context_container,
                )
                self._call_directive(context)
                new_container = self._preprocess(context.container)
                result |= new_container
            else:
                result[key] = self._preprocess(value)
        return result

    async def _preprocess_async(self, container: Any) -> Any:
        if not is_dict_like(container):
            if is_list_like(container):
                return [await self._preprocess_async(v) for v in container]
            return container

        result: dict[str, Any] = {}

        for key, value in sorted(
            cast(dict[str, Any], container).items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                actual_key = key.lstrip(self.extension_prefix)
                overridden = result.get(actual_key, {})
                if not is_dict_like(overridden):
                    raise ConfigPreprocessingError(
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {actual_key!r} "
                        f"is not a dictionary"
                    )
                replacement = overridden | value
                result[actual_key] = await self._preprocess_async(replacement)
            elif key.startswith(self.directive_prefix):
                directive_name, arguments = parse_directive_call(
                    self.directive_prefix, key
                )
                context_container = container.copy()
                del context_container[key]
                context = DirectiveContext(
                    directive=directive_name,
                    key=key,
                    prefix=self.directive_prefix,
                    arguments=arguments,
                    snippet=value,
                    container=context_container,
                )
                await self._call_directive_async(context)
                new_container = await self._preprocess_async(context.container)
                result |= new_container
            else:
                result[key] = await self._preprocess_async(value)
        return result

    def _call_directive(self, context: DirectiveContext) -> None:
        handler = self._directive_handlers.get(context.directive)
        if handler is None:
            raise ConfigPreprocessingError(
                f"unknown preprocessing directive: {context.directive!r}"
            )
        handler(self, context)

    async def _call_directive_async(self, context: DirectiveContext) -> None:
        handler = self._async_directive_handlers.get(context.directive)
        if handler is None:
            raise ConfigPreprocessingError(
                f"unknown preprocessing directive: {context.directive!r}"
            )
        await handler(self, context)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls._directive_handlers is None:
            cls._directive_handlers = {}
        else:
            cls._directive_handlers = cls._directive_handlers.copy()
        if cls._async_directive_handlers is None:
            cls._async_directive_handlers = {}
        else:
            cls._async_directive_handlers = cls._async_directive_handlers.copy()
        for _name, func in cls.__dict__.items():
            if hasattr(func, EXECUTES_DIRECTIVES):
                for directive_name in getattr(func, EXECUTES_DIRECTIVES):
                    cls._directive_handlers[directive_name] = func
            elif hasattr(func, EXECUTES_DIRECTIVES_ASYNC):
                for directive_name in getattr(func, EXECUTES_DIRECTIVES_ASYNC):
                    cls._async_directive_handlers[directive_name] = func

    @classmethod
    def register_directive(cls, name: str, func: Any) -> None:
        if cls._directive_handlers is None:
            cls._directive_handlers = {}
        cls._directive_handlers[name] = func

    @classmethod
    def directive(cls, directive_name: str, arguments: list[str] | None = None) -> str:
        """
        Create a directive call.

        Parameters
        ----------
        directive_name
            The name of the directive.
        arguments
            The arguments to pass to the directive.

        Returns
        -------
        The directive call.
        """
        if arguments is None:
            arguments = []

        def _fmt_argument(argument: str) -> str:
            if '"' in argument:
                argument = argument.replace("\\", "\\\\")
                return f'"{argument}"'
            return argument

        if isinstance(directive_name, enum.Enum):
            directive_name = directive_name.value

        fmt_arguments = (
            ",".join(map(_fmt_argument, arguments)).join("()") if arguments else ""
        )
        return cls.directive_prefix + directive_name + fmt_arguments


class Directives(str, enum.Enum):
    EXTEND = "extend"
    INCLUDE = "include"
    COPY = "copy"
    PROCESSOR = "processor"
    DEFINE = "define"


class Processor(BaseProcessor[ConfigModelT]):
    directive_prefix = "^"
    extension_prefix = "+"
    route_separator: ClassVar[str] = ":"

    @directive(Directives.EXTEND)
    def extend(self, ctx: DirectiveContext) -> None:
        """
        Extend a configuration with another configuration.
        Recursively preprocess the referenced configuration.
        Preserve information about the referenced configuration.

        Visual example
        --------------

        With `base.yaml` containing:
        ```yaml
        section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^extend: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        ^extend: base.yaml
        +section:
          foo: 3
        ```
        """
        self._substitute(ctx, preprocess=True, preserve=True)

    @directive(Directives.INCLUDE)
    def include(self, ctx: DirectiveContext) -> None:
        """
        Include a configuration in another configuration.
        Recursively preprocess the referenced configuration.
        Do not preserve information about the referenced configuration.

        Visual example
        --------------
        With `biz.yaml` containing:

        ```yaml
        section:
          biz: 3
        ```

        and `base.yaml` containing:

        ```yaml
        ^extend: biz.yaml
        +section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^include: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        ^extend: biz.yaml
        +section:
          bar: 2
          foo: 3
        ```
        """
        self._substitute(ctx, preprocess=True, preserve=False)

    @directive(Directives.COPY)
    def copy(self, ctx: DirectiveContext) -> None:
        """
        Copy a configuration and paste into another configuration.
        This is just a literal copy-paste.
        Do not preprocess the referenced configuration.
        Do not preserve information about the referenced configuration.

        Visual example
        --------------
        With `base.yaml` containing:

        ```yaml
        section:
          foo: 1
          bar: 2
        ```

        and `config.yaml` containing:

        ```yaml
        ^copy: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        section:
          foo: 3
          bar: 2
        ```
        """
        self._substitute(ctx, preprocess=False, preserve=False)

    @directive(Directives.EXTEND)
    async def extend_async(self, ctx: DirectiveContext) -> None:
        """
        Extend a configuration with another configuration asynchronously.
        For more information see `extend`.
        """
        await self._substitute_async(ctx, preprocess=True, preserve=True)

    @directive(Directives.INCLUDE)
    async def include_async(self, ctx: DirectiveContext) -> None:
        """
        Include a configuration in another configuration asynchronously.
        For more information see `include`.
        """
        await self._substitute_async(ctx, preprocess=True, preserve=False)

    @directive(Directives.COPY)
    async def copy_async(self, ctx: DirectiveContext) -> None:
        """
        Copy a configuration and paste into another configuration asynchronously.
        For more information see `copy`.
        """
        await self._substitute_async(ctx, preprocess=False, preserve=False)

    def _get_substitution_info(
        self, ctx: DirectiveContext, *, preserve: bool
    ) -> tuple[
        ConfigManager[ConfigModelT], ConfigManager[ConfigModelT], SupportsRoute | None
    ]:
        manager_class = type(self.manager)

        if len(ctx.arguments):
            msg = f"{ctx.directive!r} directive takes no () arguments"
            raise ConfigPreprocessingError(msg)

        if preserve and ctx.has_duplicates(require_same_arguments=False):
            msg = (
                f"using more than one {ctx.directive!r} directive "
                "in the same section is not allowed"
            )
            raise ConfigPreprocessingError(msg)

        manager, route = manager_class.from_directive_context(
            ctx, route_separator=self.route_separator
        )

        if manager.resource == self.manager.resource:
            raise ConfigPreprocessingError(
                f"{manager.resource} tried to {ctx.directive!r} on itself"
            )

        actual_manager = manager
        if manager.relative:
            parent = cast(pathlib.Path, self.manager.resource).parent
            child = cast(pathlib.Path, manager.resource)

            actual_manager = copy.copy(manager)
            actual_manager.resource = parent / child

        return actual_manager, manager, route

    def _substitute(
        self, ctx: DirectiveContext, *, preprocess: bool, preserve: bool
    ) -> None:
        actual_mgr, mgr, route = self._get_substitution_info(ctx, preserve=preserve)

        with actual_mgr.processor_open_resource() as reader:
            source = mgr.load_into_dict(reader.read(), preprocess=preprocess)

        self._substitute_impl(
            ctx,
            route,
            source=source,
            manager=mgr,
            preprocess=preprocess,
            preserve=preserve,
        )

    async def _substitute_async(
        self, ctx: DirectiveContext, *, preprocess: bool, preserve: bool
    ) -> None:
        actual_mgr, mgr, route = self._get_substitution_info(ctx, preserve=preserve)

        async with actual_mgr.processor_open_resource_async() as reader:
            source = mgr.load_into_dict(await reader.read(), preprocess=preprocess)

        self._substitute_impl(
            ctx,
            route,
            source=source,
            manager=mgr,
            preprocess=preprocess,
            preserve=preserve,
        )

    @staticmethod
    def _substitute_impl(  # noqa: PLR0913
        ctx: DirectiveContext,
        route: SupportsRoute | None,
        *,
        source: dict[str, Any],
        manager: ConfigManager[ConfigModelT],
        preprocess: bool,
        preserve: bool,
    ) -> None:
        from configzen.config import CONTEXT, Context, at

        if route:
            source = at(source, route, manager=manager)
            if not is_dict_like(source):
                raise ConfigPreprocessingError(
                    f"imported item {route!r} "
                    f"from {manager.resource} is not a dictionary"
                )

        context: Context[ConfigModelT] = Context(manager)
        ctx.container = source | ctx.container

        if preserve:
            ctx.container |= {
                CONTEXT: context,
                EXPORT: ExportMetadata(
                    route=str(route),
                    context=context,
                    preprocess=preprocess,
                    key_order=list(ctx.container),
                ),
            }

    @classmethod
    def _export(
        cls,
        state: dict[str, Any],
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        """
        Exports model state preserving substition directive calls in the model state.

        Parameters
        ----------
        metadata
        state
        """
        from configzen.config import CONTEXT, at, pre_serialize

        overrides = {}

        route = metadata["route"]
        context = metadata["context"]
        key_order = metadata["key_order"]
        manager = context.manager

        with manager.processor_open_resource() as reader:
            # Here we intentionally always preprocess the loaded configuration.
            loaded = manager.load_into_dict(reader.read())

            if route:
                loaded = at(loaded, route, manager=manager)

        substituted_values = loaded.copy()

        missing = object()

        for key, value in loaded.items():
            counterpart_value = state.pop(key, missing)
            if counterpart_value is missing:
                continue
            counterpart_value = pre_serialize(counterpart_value)

            if is_dict_like(value):
                if EXPORT in value:
                    value.pop(CONTEXT, None)
                    cls.export(value, metadata=value.pop(EXPORT))
                overrides_for_key = {
                    sub_key: comp
                    for sub_key, comp in counterpart_value.items()
                    if (
                        (orig := value.get(sub_key, missing)) is missing or orig != comp
                    )
                }
                if overrides_for_key:
                    export_key = manager.processor_class.extension_prefix + key
                    overrides[export_key] = overrides_for_key

            elif is_list_like(value):
                cls.export(value, metadata=None)

            elif counterpart_value != value:
                overrides[key] = counterpart_value
                del substituted_values[key]

        for value in state.values():
            cls.export(value, metadata=None)

        cls._export_finalize(
            context=context,
            state=state,
            overrides=overrides,
            values=substituted_values,
            route=route,
            key_order=key_order,
        )

    @classmethod
    async def _export_async(
        cls,
        state: dict[str, Any],
        metadata: ExportMetadata[ConfigModelT],
    ) -> None:
        """
        Exports model state preserving substition directive calls in the model state.

        Parameters
        ----------
        metadata
        state
        """
        from configzen.config import CONTEXT, at, pre_serialize

        overrides = {}

        route = metadata["route"]
        context = metadata["context"]
        key_order = metadata["key_order"]
        manager = context.manager

        async with manager.processor_open_resource_async() as reader:
            # Here we intentionally always preprocess the loaded configuration.
            loaded = await manager.load_into_dict_async(await reader.read())

            if route:
                loaded = at(loaded, route, manager=manager)

        substituted_values = loaded.copy()

        missing = object()

        for key, value in loaded.items():
            counterpart_value = state.pop(key, missing)
            if counterpart_value is missing:
                continue
            counterpart_value = pre_serialize(counterpart_value)

            if is_dict_like(value):
                if EXPORT in value:
                    value.pop(CONTEXT, None)
                    await cls.export_async(value, metadata=value.pop(EXPORT))
                overrides_for_key = {
                    sub_key: comp
                    for sub_key, comp in counterpart_value.items()
                    if (
                        (orig := value.get(sub_key, missing)) is missing or orig != comp
                    )
                }
                if overrides_for_key:
                    export_key = manager.processor_class.extension_prefix + key
                    overrides[export_key] = overrides_for_key

            elif is_list_like(value):
                await cls.export_async(value, metadata=None)

            elif counterpart_value != value:
                overrides[key] = counterpart_value
                del substituted_values[key]

        for value in state.values():
            await cls.export_async(value, metadata=None)

        cls._export_finalize(
            context=context,
            state=state,
            overrides=overrides,
            values=substituted_values,
            route=route,
            key_order=key_order,
        )

    @classmethod
    def _export_finalize(  # noqa: PLR0913
        cls,
        context: BaseContext[ConfigModelT],
        *,
        state: dict[str, Any],
        overrides: dict[str, Any],
        values: dict[str, Any],
        route: str | None,
        key_order: list[str],
    ) -> None:
        # TODO: Optimize. We iterate over the same too many times.

        state |= overrides
        extras: dict[str, Any] = {
            key: state.pop(key) for key in set(state) if key not in key_order
        }

        if values:
            substitution_directive = cls.directive(Directives.EXTEND)
            resource = str(context.manager.resource)
            if route:
                resource = cls.route_separator.join((resource, route))
            # Put the substitution directive at the beginning of the state in-place.
            state |= {substitution_directive: resource} | {
                key: state.pop(key) for key in set(state)
            }

        # Preserve the order of keys in the original configuration.
        for key in filter(state.__contains__, key_order):
            state[key] = state.pop(key)

        state |= extras
