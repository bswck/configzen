from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypedDict, TypeVar, cast

from anyconfig.utils import is_dict_like, is_list_like

if TYPE_CHECKING:
    from configzen.config import AnyContext, ConfigResource


__all__ = (
    "DirectiveContext",
    "directive",
    "Processor",
)


DirectiveT = TypeVar("DirectiveT")
ProcessorT = TypeVar("ProcessorT", bound="_BaseProcessor")

IMPORT_METADATA: str = "__configzen_import__"
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
        directive_name = directive_name[len(prefix) :].casefold()
        if directive_name.endswith(tokens.RPAREN):
            try:
                lpar = directive_name.index(tokens.LPAREN)
            except ValueError:
                raise ValueError(f"invalid directive call: {directive_name}") from None
            (directive_name, raw_argument_string) = (
                directive_name[:lpar],
                directive_name[lpar + 1 : -1],
            )
            arguments = _parse_argument_string(raw_argument_string, tokens)

        if not directive_name.isidentifier():
            raise ValueError(f"Invalid directive name: {directive_name}")
    return directive_name, arguments


class ImportMetadata(TypedDict):
    """
    Metadata for an import.

    Attributes
    ----------
    route
        The route to import from.
    context
        The context attached to the import.
    """

    route: str | None
    context: AnyContext


class _BaseProcessor:
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
        resource: ConfigResource,
        dict_config: dict[str, Any],
    ) -> None:
        self.resource = resource
        self.dict_config = dict_config

    def preprocess(self) -> dict[str, Any]:
        """
        Parse the dictionary config and return the parsed config,
        ready for instantiating the model.

        Returns
        -------
        The parsed config.
        """
        return self._preprocess(self.dict_config)

    @classmethod
    def export(
        cls,
        state: dict[str, Any],
        metadata: ImportMetadata,
    ) -> None:
        """
        Exports model state preserveing /extends directive calls in a model state.

        Parameters
        ----------
        metadata
        state
        """
        from configzen.config import convert, select_scope

        overrides = {}

        context = metadata["context"]
        route = metadata["route"]
        resource = context.resource

        with resource.open_resource() as reader:
            imported = resource.load_into_dict(reader.read())
            if route:
                imported = select_scope(imported, route, resource=resource)

        imported_values = imported.copy()

        missing = object()
        for key, value in imported.items():
            counterpart_value = state.get(key, missing)
            if counterpart_value is missing:
                continue
            counterpart_value = convert(counterpart_value)
            if is_dict_like(value):
                overrides_for_key = {
                    k: cv
                    for k, v in value.items()
                    if (
                        (cv := counterpart_value.get(k, missing)) is not missing
                        and v != cv
                    )
                }
                if overrides_for_key:
                    overrides["+" + key] = overrides_for_key
            else:
                counterpart_value = convert(counterpart_value)
                if counterpart_value != value:
                    overrides[key] = counterpart_value
                    del imported_values[key]

        state.clear()

        if imported_values:
            # If no imported values are left,
            # the import directive is not needed
            arguments = [] if route is None else [route]
            import_directive = cls.directive(Directives.EXTENDS, arguments)
            state |= {import_directive: context.resource.resource}

        state |= overrides

    def _preprocess(self, container: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}

        for key, value in sorted(
            container.items(),
            key=lambda item: item[0] == self.directive_prefix,
        ):
            if key.startswith(self.extension_prefix):
                k = key.lstrip(self.extension_prefix)
                overridden = result.get(k, {})
                if not is_dict_like(overridden):
                    raise ValueError(
                        f"{self.extension_prefix} can be used only for overriding "
                        f"dictionary sections but item at {k!r} is not a dictionary"
                    )
                result[k] = overridden | value
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
    EXTENDS = "extends"


class Processor(_BaseProcessor):
    directive_prefix = "/"
    extension_prefix = "+"

    @directive(Directives.EXTENDS)
    def _call_extends(self, directive_context: DirectiveContext) -> None:
        from configzen.config import CONTEXT, Context, select_scope

        resource_class = type(self.resource)
        if len(directive_context.arguments) > 1:
            raise ValueError("'extends' directive can select only one section")
        if directive_context.has_duplicates():
            raise ValueError("duplicate 'extends' directive")
        if isinstance(directive_context.snippet, str):
            resource = resource_class(directive_context.snippet)
        elif is_dict_like(directive_context.snippet):
            resource = resource_class(**directive_context.snippet)
        elif is_list_like(directive_context.snippet):
            resource = resource_class(*cast(list, directive_context.snippet))
        else:
            raise ValueError(
                f"invalid snippet for import directive: {directive_context.snippet!r}"
            )
        if resource.resource == self.resource.resource:
            raise ValueError(f"{resource.resource} tried to import itself")
        with resource.open_resource() as reader:
            imported_data = resource.load_into_dict(reader.read())
        import_route = (
            directive_context.arguments[0] if directive_context.arguments else None
        )
        if import_route:
            try:
                imported_data = select_scope(imported_data, import_route)
            except LookupError:
                raise LookupError(
                    f"attempted to import item at {import_route!r} "
                    f"from {resource.resource} that does not exist"
                ) from None
            if not is_dict_like(imported_data):
                raise ValueError(
                    f"imported item {import_route!r} "
                    f"from {resource.resource} is not a dictionary"
                )
        context: Context = Context(resource)
        directive_context.container = (
            imported_data
            | directive_context.container
            | {
                CONTEXT: context,
                IMPORT_METADATA: ImportMetadata(route=import_route, context=context),
            }
        )
