"""Routes creation and parsing."""

from __future__ import annotations

from functools import reduce, singledispatchmethod
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Generic,
    Type,
    TypeVar,
    Union,
    get_origin,
)

from class_singledispatch import class_singledispatch

from configzen.errors import LinkedRouteError, RouteError
from configzen.typedefs import ConfigObject

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typing_extensions import Self, TypeAlias


__all__ = (
    "GetAttr",
    "GetItem",
    "Route",
    "RouteLike",
    "Step",
)

RouteLike: TypeAlias = Union[
    str,
    int,
    "list[str | int]",
    "tuple[str | int, ...]",
    "list[Step[Any]]",
    "tuple[Step[Any], ...]",
    "Route",
    "Step[Any]",
]

_KT = TypeVar("_KT")


class Step(Generic[_KT]):
    """
    A configuration route step.

    Do not use this class directly. Use GetAttr or GetItem instead.
    """

    key: _KT

    def __init__(self, key: _KT, /) -> None:
        self.key = key

    def __eq__(self, other: object) -> bool:
        """Compare this step to another step."""
        if isinstance(other, Step):
            return (
                issubclass(type(other), type(self))
                or issubclass(type(self), type(other))
            ) or self.key == other.key
        return NotImplemented

    def get(self, _: Any, /) -> object:
        """Perform a get operation."""
        raise NotImplementedError

    def set(self, _: Any, __: object, /) -> None:
        """Perform a set operation."""
        raise NotImplementedError

    def __call__(self, obj: Any, /) -> object:
        """Perform a get operation."""
        return self.get(obj)

    def __repr__(self) -> str:
        """Represent this step in a string."""
        return f"{type(self).__name__}({self.key!r})"


class GetAttr(Step[str]):
    """
    A route step that gets an attribute from an object.

    The argument is used as an attribute name.
    """

    def get(self, target: Any, /) -> object:
        """Get an attribute from an object."""
        return getattr(target, self.key)

    def set(self, target: Any, value: object, /) -> None:
        """Set an attribute in an object."""
        setattr(target, self.key, value)

    def __str__(self) -> str:
        """Compose this step into a string."""
        return str(self.key).replace(Route.TOKEN_DOT, r"\.")


class GetItem(Step[Union[int, str]]):
    r"""
    A route step that gets an item from an object.

    If the argument is a string, it is used checked for being a digit.
    Unless explicitly escaped, if it is a digit, it is casted to an integer.
    Otherwise, it is used as is.
    """

    def __init__(self, key: int | str, /, *, ignore_digit: bool = False) -> None:
        self.escape = False
        if isinstance(key, str) and key.isdigit():
            if ignore_digit:
                self.escape = True
            else:
                key = int(key)
        super().__init__(key)

    def get(self, target: Any, /) -> object:
        """Get an item from an object."""
        return target[self.key]

    def set(self, target: Any, value: object, /) -> None:
        """Set an item in an object."""
        target[self.key] = value

    def __str__(self) -> str:
        """Compose this step into a string."""
        argument = str(self.key)
        if self.escape:
            argument = Route.TOKEN_ESCAPE + argument
        return argument.join(
            Route.TOKEN_ENTER + Route.TOKEN_LEAVE,
        ).replace(Route.TOKEN_DOT, r"\.")


def _route_decompose(  # noqa: C901, PLR0912, PLR0915
    route: str,
    *,
    dot: str,
    escape: str,
    enter: str,
    leave: str,
) -> list[Step[Any]]:
    """
    Decompose a route into a list of steps.

    Parameters
    ----------
    route
        A route to decompose.
    dot
        A token used to separate steps.
    escape
        A token used to escape a token.
    enter
        A token used to enter an item.
    leave
        A token used to exit an item.

    Returns
    -------
    List of steps.

    """
    if not route.endswith(dot):
        route += dot

    key = ""
    entered: int | None = None
    left: bool = False
    steps: list[Step[Any]] = []
    emit = steps.append
    escaping = False
    escaped = False
    step: Step[Any]

    for index, token in enumerate(route):
        if escaping:
            key += token
            escaping = False
            escaped = True
            continue
        is_last = index == len(route) - 1
        if token == dot:
            if entered is not None:
                key += token
            else:
                if left:
                    step = GetItem(key, ignore_digit=escaped)
                    left = False
                else:
                    step = GetAttr(key)
                escaped = False
                emit(step)
                key = ""
        elif token == escape:
            if is_last:
                key += token
            else:
                escaping = True
        elif token == enter:
            if entered is not None:
                msg = f"Already seen {enter!r} that was not closed with {leave!r}"
                raise RouteError(msg, route=route, index=index)
            if key or index == 0:
                entered = index
                if index:
                    if left:
                        step = GetItem(key, ignore_digit=escaped)
                        left = False
                    else:
                        step = GetAttr(key)
                    escaped = False
                    emit(step)
                    key = ""
            else:
                msg = f"No key between {route[index-1]!r} and {token!r}"
                raise RouteError(msg, route=route, index=index)
        elif token == leave:
            if entered is None:
                msg = f"{token!r} not preceded by {enter!r} token"
                raise RouteError(msg, route=route, index=index)
            entered = None
            left = True
        else:
            key += token
        if is_last and entered is not None:
            msg = f"Expected {leave!r} token"
            raise RouteError(msg, route=route, index=entered)
    return steps


class Route:
    r"""
    Routes are, lists of steps that are used to access values in a configuration.

    Each step is either a key or an index.

    A route can be created from a string, a list of steps, or another route.

    Examples
    --------
    >>> route = Route("a.b.c")
    >>> route
    <Route 'a.b.c'>
    >>> route.steps
    [GetAttr('a'), GetAttr('b'), GetAttr('c')]

    Parameters
    ----------
    route
        A route to parse.
    allow_empty
        Whether to allow empty routes.

    """

    TOKEN_DOT: ClassVar[str] = "."
    TOKEN_ESCAPE: ClassVar[str] = "\\"
    TOKEN_ENTER: ClassVar[str] = "["
    TOKEN_LEAVE: ClassVar[str] = "]"

    TOKENS: ClassVar[tuple[str, str, str, str]] = (
        TOKEN_DOT,
        TOKEN_ESCAPE,
        TOKEN_ENTER,
        TOKEN_LEAVE,
    )

    def __init__(
        self,
        route: RouteLike,
        *,
        allow_empty: bool = False,
    ) -> None:
        steps = self.parse(route)
        if not (allow_empty or steps):
            msg = "Empty configuration route"
            raise ValueError(msg)
        self.__steps = tuple(steps)

    @property
    def steps(self) -> list[Step[Any]]:
        """Get all steps in this route."""
        return list(self.__steps)

    def __hash__(self) -> int:
        """Get a hash of this route."""
        return hash(self.__steps)

    @classmethod
    def parse(cls, route: RouteLike) -> list[Step[Any]]:
        """
        Parse a route into steps.

        Parameters
        ----------
        route
            The route to parse.

        Returns
        -------
        List of steps.

        """
        if isinstance(route, Step):
            return [route]
        if isinstance(route, Route):
            return route.steps
        if isinstance(route, (tuple, list)):
            patched_route: list[Step[Any]] = []
            for element in route:
                if isinstance(element, (str, int)):
                    try:
                        patched_element = next(iter(cls.parse(element)))
                    except StopIteration:
                        continue
                else:
                    patched_element = element
                patched_route.append(patched_element)
            return patched_route
        if isinstance(route, int):
            return [GetItem(route)]
        if isinstance(route, str):
            return cls.decompose(route)
        msg = f"Invalid route type {type(route)!r}"
        raise TypeError(msg)

    @classmethod
    def decompose(cls, route: str) -> list[Step[Any]]:
        """
        Decompose a route into a list of steps.

        Parameters
        ----------
        route
            A route to decompose.

        Returns
        -------
        List of steps.

        """
        if not route:
            return []

        dot, escape, enter, leave = cls.TOKENS

        return _route_decompose(
            route,
            dot=dot,
            escape=escape,
            enter=enter,
            leave=leave,
        )

    def compose(self) -> str:
        """Compose this route into a string."""
        composed = ""
        steps = self.__steps
        for index, step in enumerate(steps):
            composed += str(step)
            if index < len(steps) - 1:
                ahead = steps[index + 1]
                if isinstance(ahead, GetAttr):
                    composed += self.TOKEN_DOT
        return composed

    def enter(self, subroute: RouteLike) -> Route:
        """
        Enter a subroute.

        Parameters
        ----------
        subroute
            A subroute to enter.

        """
        return type(self)(self.steps + self.parse(subroute))

    def get(self, obj: Any, /) -> object:
        """
        Get an object at the end of this route.

        Parameters
        ----------
        obj
            An object to dive in.

        Returns
        -------
        The result of visiting the object.

        """
        return reduce(lambda obj, step: step(obj), self.__steps, obj)

    def set(self, obj: Any, value: object, /) -> None:
        """
        Set an object pointed to by this route.

        Parameters
        ----------
        obj
            An object to dive in.

        value
            A value to set.

        Returns
        -------
        The result of visiting the object.

        """
        steps = self.steps
        last_step = steps.pop()
        last_step.set(
            reduce(lambda obj, step: step(obj), steps, obj),
            value,
        )

    def __eq__(self, other: object) -> bool:
        """
        Compare this route to another route.

        Parameters
        ----------
        other
            Another route to compare to.

        """
        if isinstance(other, Route):
            return self.steps == other.steps
        if isinstance(other, str):
            return self.steps == self.decompose(other)
        if isinstance(other, (tuple, list)):
            return self.steps == self.parse(other)
        return NotImplemented

    def __str__(self) -> str:
        """Compose this route into a string."""
        return self.compose()

    def __iter__(self) -> Iterator[Step[Any]]:
        """Yield all steps in this route."""
        yield from self.__steps

    def __repr__(self) -> str:
        """Represent this route in a string."""
        return f"<{type(self).__name__} {self.compose()!r}>"


EMPTY_ROUTE: Route = Route("", allow_empty=True)


@class_singledispatch
def advance_linked_route(
    _current_head: Type[object],  # noqa: UP006
    _annotation: Any,
    _step: Step[object],
) -> Any:
    """Move one step forward in a linked route."""
    return _AnyHead


class _AnyHead:
    pass


class LinkedRoute(Generic[ConfigObject]):
    __head_class: type[Any]

    def __init__(
        self,
        config_class: type[ConfigObject],
        route: RouteLike,
    ) -> None:
        self.__head_class = self.__config_class = config_class
        self.__annotation = None
        self.__route = EMPTY_ROUTE
        for step in Route(route):
            self.__step(step)

    def __type_check(self, cls: type[object]) -> bool:
        # Can't use TypeGuard/TypeIs here, sorry
        return isinstance(self.__head_class, type) and issubclass(
            self.__head_class,
            cls,
        )

    @singledispatchmethod
    def __step(self, step: Step[Any]) -> None:
        raise NotImplementedError

    @__step.register
    def __getattr(self, step: GetAttr) -> None:
        from configzen.config import BaseConfig

        head = self.__head_class

        if (
            self.__type_check(BaseConfig)
            and step.key not in head.model_fields
            and head is not _AnyHead
        ):
            if head.model_config["extra"] == "allow":
                self.__head_class = _AnyHead

            msg = f"Cannot use {step!r} on {self.__head_class.__name__!r}"
            raise LinkedRouteError(
                msg,
                config_class=self.__config_class,
                route=str(self.__route),
            )

        self.__annotation = annotation = advance_linked_route(
            self.__head_class,
            self.__annotation,
            step,
        )
        self.__head_class = get_origin(annotation) or annotation
        self.__route = self.__route.enter(step)

    @__step.register
    def __getitem(self, step: GetItem) -> None:
        from configzen.config import BaseConfig

        if self.__type_check(BaseConfig):
            msg = f"Cannot use {step!r} on a configuration class"
            raise LinkedRouteError(
                msg,
                config_class=self.__config_class,
                route=str(self.__route),
            )

        if not hasattr(self.__head_class, "__getitem__"):
            msg = f"Cannot use {step!r} on {self.__head_class.__name__!r}"
            raise LinkedRouteError(
                msg,
                config_class=self.__config_class,
                route=str(self.__route),
            )

        self.__annotation = annotation = advance_linked_route(
            self.__head_class,
            self.__annotation,
            step,
        )
        self.__head_class = get_origin(annotation) or annotation
        self.__route = self.__route.enter(step)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LinkedRoute):
            return (
                self.__config_class is other.__config_class
                and self.__route == other.__route
            )
        return NotImplemented

    def __getitem__(self, item: int | str) -> Self:
        self.__step(GetItem(item))
        return self

    def __getattr__(self, item: str) -> Self:
        self.__step(GetAttr(item))
        return self

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.__route!r}>"
