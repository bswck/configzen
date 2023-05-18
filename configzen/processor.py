from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypedDict, TypeVar

from anyconfig.utils import is_dict_like, is_list_like

if TYPE_CHECKING:
    from configzen.config import AnyContext, ConfigLoader


__all__ = (
    "DirectiveContext",
    "directive",
    "Processor",
)


DirectiveT = TypeVar("DirectiveT")
ProcessorT = TypeVar("ProcessorT", bound="Processor")

SUBST_METADATA: str = "__configzen_substitute__"
EXECUTES_DIRECTIVES: str = "__configzen_executes_directives__"


DirectiveHandlerT = Callable[[ProcessorT, "DirectiveContext"], None]


def directive(
    name: str | enum.Enum,
) -> Callable[[DirectiveHandlerT], DirectiveHandlerT]:
    """
    Decorator for creating processor directives.

    Parameters
    ----------
    name
        The name of the directive.

    Returns
    -------
    The decorated function.
    """
    if isinstance(name, enum.Enum):
        name = name.value.casefold()

    def decorator(func: DirectiveHandlerT) -> DirectiveHandlerT:
        if not hasattr(func, EXECUTES_DIRECTIVES):
            setattr(func, EXECUTES_DIRECTIVES, set())
        getattr(func, EXECUTES_DIRECTIVES).add(name)
        return func

    return decorator


@dataclasses.dataclass
class DirectiveContext(Generic[DirectiveT]):
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

    directive: DirectiveT
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
            if directive_name == self.key:
                if require_same_arguments and arguments != self.arguments:
                    continue
                return True
        return False


class Tokens(str, enum.Enum):
    LPAREN = "("
    RPAREN = ")"
    COMMA = ",;"
    STRING = "\"'"
    ESCAPE = "\\"


class ArgumentSyntaxError(ValueError):
    """
    Raised when there is a syntax error in an argument.
    """


def _parse_argument_string_impl(
    raw_argument_string: str,
    tokens: type[Tokens] = Tokens,
) -> list[str]:
    prev_ch = None

    string_ctx = None
    escape_ctx = False
    arguments: list[str] = []
    argument = ""
    emit = arguments.append
    explicit_strings_ctx = True

    tok_escape = tokens.ESCAPE
    tok_string = tokens.STRING
    tok_comma = tokens.COMMA

    for no, ch in enumerate(raw_argument_string, start=1):
        if escape_ctx:
            escape_ctx = False
            argument += ch
        elif ch in tok_escape:
            escape_ctx = True
        elif ch in tok_string:
            if string_ctx and not explicit_strings_ctx:
                raise ArgumentSyntaxError(
                    f"Implicit string closed with explicit string character {ch}",
                    (no, ch),
                )
            explicit_strings_ctx = True
            if string_ctx is None:
                # we enter a string
                string_ctx = ch
            elif string_ctx == ch:
                # we exit a string
                string_ctx = None
            else:
                # we are in a string
                argument += ch
        elif ch in tok_comma:
            if string_ctx:
                if not explicit_strings_ctx:
                    string_ctx = None
                    explicit_strings_ctx = True
                    emit(argument)
                    argument = ""
            else:
                if prev_ch in {*tok_comma, None}:
                    raise ArgumentSyntaxError("Empty argument", (no, ch))
                emit(argument)
                argument = ""
                explicit_strings_ctx = False
        elif not string_ctx and not ch.isspace():
            if prev_ch in {*tok_comma, None}:
                string_ctx = ch
                argument += ch
                explicit_strings_ctx = False
            if explicit_strings_ctx:
                raise ArgumentSyntaxError(
                    "Unexpected character after explicit string", (no, ch)
                )
        else:
            argument += ch
        prev_ch = ch
    return arguments


def _parse_argument_string(
    raw_argument_string: str,
    tokens: type[Tokens] = Tokens,
) -> list[str]:
    """Half for jokes, half for serious use"""

    no = 0
    tok_comma = tokens.COMMA

    if any(raw_argument_string.endswith(tok) for tok in tok_comma):
        raw_argument_string = raw_argument_string[:-1]
    raw_argument_string += tok_comma[0]

    # Parse arguments with respect to strings
    try:
        arguments = _parse_argument_string_impl(raw_argument_string, tokens)
    except ArgumentSyntaxError as e:
        msg, (no, ch) = e.args
        charlist = ["~"] * (len(raw_argument_string) + 1)
        displayed_argument_string = (
            raw_argument_string[:-1]
            if no == len(raw_argument_string) + 1
            else raw_argument_string
        ).join((tokens.LPAREN[0], tokens.RPAREN[0]))
        charlist[no] = "^"
        indicator = "".join(charlist)
        raise ArgumentSyntaxError(
            "\n" + displayed_argument_string + "\n" + indicator + "\n" + msg
        ) from None

    return arguments


def parse_directive_call(
    prefix: str,
    directive_name: str,
    tokens: type[Tokens] = Tokens,
) -> tuple[str, list[str]]:
    arguments = []
    if directive_name.startswith(prefix):
        directive_name = directive_name[len(prefix):].casefold()

        if directive_name.endswith(tokens.RPAREN):
            try:
                lpar = directive_name.index(tokens.LPAREN)
            except ValueError:
                raise ValueError(f"invalid directive call: {directive_name}") from None
            (directive_name, raw_argument_string) = (
                directive_name[:lpar],
                directive_name[lpar + 1: -1],
            )
            arguments = _parse_argument_string(raw_argument_string, tokens)

        if not directive_name.isidentifier():
            raise ValueError(f"Invalid directive name: {directive_name}")

    return directive_name, arguments


class SubstitutionMetadata(TypedDict):
    """
    Metadata for a substitution directive: either EXPAND or EXTEND call.

    Attributes
    ----------
    route
        The route to import from.
    context
        The context attached to the import.
    """

    route: str | None
    context: AnyContext
    preprocess: bool


class BaseProcessor:
    """
    Processor that executes directives.

    Attributes
    ----------
    dict_config
        The dictionary config to parse and update.
    directive_prefix
        The prefix for directives.
    """

    _directive_handlers: dict[str, DirectiveHandlerT] = None  # type: ignore[assignment]
    directive_prefix: ClassVar[str]
    extension_prefix: ClassVar[str]

    def __init__(
        self,
        resource: ConfigLoader,
        dict_config: dict[str, Any],
    ) -> None:
        self.loader = resource
        self.dict_config = dict_config

    @classmethod
    def export(
        cls,
        state: dict[str, Any],
        metadata: SubstitutionMetadata,
    ) -> None:
        pass

    def preprocess(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config,
        ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return self._preprocess(self.dict_config)

    def _preprocess(self, container: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}

        for key, value in sorted(
            container.items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                actual_key = key.lstrip(self.extension_prefix)
                overridden = result.get(actual_key, {})
                if not is_dict_like(overridden):
                    raise ValueError(
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {actual_key!r} "
                        f"is not a dictionary"
                    )
                result[actual_key] = overridden | value
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
            elif is_dict_like(value):
                result[key] = self._preprocess(value)
            elif is_list_like(value):
                result[key] = [
                    self._preprocess(v) if isinstance(v, dict) else v for v in value
                ]
            else:
                result[key] = value
        return result

    def _call_directive(self, context: DirectiveContext) -> None:
        handler = self._directive_handlers.get(context.directive)
        if handler is None:
            raise ValueError(f"unknown processor directive: {context.directive!r}")
        handler(self, context)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls._directive_handlers is None:
            cls._directive_handlers = {}
        else:
            cls._directive_handlers = cls._directive_handlers.copy()
        for _name, func in cls.__dict__.items():
            if hasattr(func, EXECUTES_DIRECTIVES):
                for directive_name in getattr(func, EXECUTES_DIRECTIVES):
                    cls._directive_handlers[directive_name] = func

    @classmethod
    def register_directive(cls, name: str, func: DirectiveHandlerT) -> None:
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

        return (
            cls.directive_prefix
            + directive_name
            + (",".join(map(_fmt_argument, arguments)).join("()") if arguments else "")
        )


class Directives(str, enum.Enum):
    EXPAND = "expand"
    EXTEND = "extend"
    INCLUDE = "include"
    COPY = "copy"
    PROCESSOR = "processor"
    DEFINE = "define"


class Processor(BaseProcessor):
    directive_prefix = "^"
    extension_prefix = "+"

    @directive(Directives.EXPAND)
    def expand(self, ctx: DirectiveContext) -> None:
        """
        Expand a configuration with another configuration.
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
        ^expand: base.yaml
        +section:
          foo: 3
        ```

        -> `load()` -> `save()` ->

        ```yaml
        ^expand: base.yaml
        +section:
          foo: 3
        ```
        """
        return self._substitute(ctx, preprocess=True, preserve=True)

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
        ^expand: biz.yaml
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
        ^expand: biz.yaml
        +section:
          bar: 2
          foo: 3
        ```
        """
        return self._substitute(ctx, preprocess=True, preserve=False)

    @directive(Directives.EXTEND)
    def extend(self, ctx: DirectiveContext) -> None:
        """
        Extend a configuration with another configuration.
        Do not preprocess the referenced configuration.
        Preserve information about the referenced configuration.

        Visual example
        --------------
        TODO
        """
        return self._substitute(ctx, preprocess=False, preserve=True)

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
        return self._substitute(ctx, preprocess=False, preserve=False)

    def _substitute(
        self,
        ctx: DirectiveContext,
        *,
        preprocess: bool,
        preserve: bool
    ) -> None:
        from configzen.config import CONTEXT, Context, select_scope

        loader_class = type(self.loader)
        if len(ctx.arguments) > 1:
            msg = f"{ctx.directive!r} directive can select only one section"
            raise ValueError(msg)
        if ctx.has_duplicates():
            msg = f"duplicate {ctx.directive!r} directive"
            raise ValueError(msg)

        loader = loader_class.from_directive_context(ctx)

        if loader.resource == self.loader.resource:
            raise ValueError(f"{loader.resource} tried to {ctx.directive!r} on itself")

        with loader.processor_open_resource() as reader:
            substituted = loader.load_into_dict(reader.read(), preprocess=preprocess)

        substitution_route = ctx.arguments[0] if ctx.arguments else None
        if substitution_route:
            try:
                substituted = select_scope(substituted, substitution_route)
            except LookupError:
                raise LookupError(
                    f"attempted to import item at {substitution_route!r} "
                    f"from {loader.resource} that does not exist"
                ) from None
            if not is_dict_like(substituted):
                raise ValueError(
                    f"imported item {substitution_route!r} "
                    f"from {loader.resource} is not a dictionary"
                )
        context: Context = Context(loader)
        ctx.container = substituted | ctx.container

        if preserve:
            ctx.container |= {
                CONTEXT: context,
                SUBST_METADATA: SubstitutionMetadata(
                    route=substitution_route,
                    context=context,
                    preprocess=preprocess
                ),
            }

    @classmethod
    def export(
        cls,
        state: dict[str, Any],
        metadata: SubstitutionMetadata,
    ) -> None:
        """
        Exports model state preserving substition directive calls in the model state.

        Parameters
        ----------
        metadata
        state
        """
        from configzen.config import convert, select_scope

        overrides = {}

        context = metadata["context"]
        route = metadata["route"]
        preprocess = metadata["preprocess"]
        loader = context.loader

        with loader.processor_open_resource() as reader:
            loaded = loader.load_into_dict(reader.read(), preprocess=preprocess)
            if route:
                loaded = select_scope(loaded, route, loader=loader)

        substituted_values = loaded.copy()

        missing = object()

        for key, value in loaded.items():
            counterpart_value = state.get(key, missing)
            if counterpart_value is missing:
                continue
            counterpart_value = convert(counterpart_value)
            if is_dict_like(value):
                overrides_for_key = {
                    sub_key: comp
                    for sub_key, orig in value.items()
                    if (
                        (comp := counterpart_value.get(sub_key, missing)) is not missing
                        and orig != comp
                    )
                }
                if overrides_for_key:
                    export_key = loader.processor_class.extension_prefix + key
                    overrides[export_key] = overrides_for_key
            else:
                counterpart_value = convert(counterpart_value)
                if counterpart_value != value:
                    overrides[key] = counterpart_value
                    del substituted_values[key]

        state.clear()

        if substituted_values:
            arguments = [] if route is None else [route]
            directive_name = Directives.EXPAND if preprocess else Directives.EXTEND
            substitution_directive = cls.directive(directive_name, arguments)
            state |= {substitution_directive: context.loader.resource}

        state |= overrides
