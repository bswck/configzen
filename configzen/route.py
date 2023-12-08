"""Route creation and parsing."""

from __future__ import annotations

from functools import reduce
from typing import TYPE_CHECKING, Any, ClassVar, Union

from configzen.errors import RouteError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typing_extensions import TypeAlias


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
    "list[Step]",
    "tuple[Step, ...]",
    "Route",
    "Step",
]


class Step:
    """
    A route step.

    Do not use this class directly. Use GetAttr or GetItem instead.
    """

    key: object

    def __init__(self, argument: object, /) -> None:
        self.key = argument

    def get(self, _: Any, /) -> object:
        """Perform a get operation."""
        raise NotImplementedError

    def set(self, _: Any, __: object, /) -> None:  # noqa: A003
        """Perform a set operation."""
        raise NotImplementedError

    def __call__(self, obj: Any, /) -> object:
        """Perform a get operation."""
        return self.get(obj)

    def __repr__(self) -> str:
        """Represent this step in a string."""
        return f"{type(self).__name__}({self.key!r})"


class GetAttr(Step):
    """
    A route step that gets an attribute from an object.

    The argument is used as an attribute name.
    """

    key: str

    def get(self, target: Any, /) -> object:
        """Get an attribute from an object."""
        return getattr(target, self.key)

    def set(self, target: Any, value: object, /) -> None:  # noqa: A003
        """Set an attribute in an object."""
        setattr(target, self.key, value)

    def __str__(self) -> str:
        """Compose this step into a string."""
        return str(self.key).replace(Route.TOKEN_DOT, r"\.")


class GetItem(Step):
    r"""
    A route step that gets an item from an object.

    If the argument is a string, it is used checked for being a digit.
    Unless explicitly escaped, if it is a digit, it is casted to an integer.
    Otherwise, it is used as is.
    """

    def __init__(self, argument: int | str, /, *, ignore_digit: bool = False) -> None:
        self.escape = False
        if isinstance(argument, str) and argument.isdigit():
            if ignore_digit:
                self.escape = True
            else:
                argument = int(argument)
        super().__init__(argument)

    def get(self, target: Any, /) -> object:
        """Get an item from an object."""
        return target[self.key]

    def set(self, target: Any, value: object, /) -> None:  # noqa: A003
        """Set an item in an object."""
        target[self.key] = value

    def __str__(self) -> str:
        """Compose this step into a string."""
        argument = str(self.key)
        if self.escape:
            argument = Route.TOKEN_ESCAPE + argument
        return argument.join(
            Route.TOKEN_GETITEM_ENTER + Route.TOKEN_GETITEM_EXIT,
        )


class Route:
    r"""
    Routes are, lists of steps that are used to access values in a configuration.

    Each step is either a key or an index.

    A route can be created from a string, a list of steps, or another route.

    Examples
    --------
    >>> route = ConfigRoute("a.b.c")
    >>> route
    ConfigRoute([GetAttr('a'), GetAttr('b'), GetAttr('c')])

    Parameters
    ----------
    route
        A route to parse.
    allow_empty
        Whether to allow empty routes.
    """

    TOKEN_DOT: ClassVar[str] = "."
    TOKEN_ESCAPE: ClassVar[str] = "\\"
    TOKEN_GETITEM_ENTER: ClassVar[str] = "["
    TOKEN_GETITEM_EXIT: ClassVar[str] = "]"

    TOKENS: ClassVar[tuple[str, str, str, str]] = (
        TOKEN_DOT,
        TOKEN_ESCAPE,
        TOKEN_GETITEM_ENTER,
        TOKEN_GETITEM_EXIT,
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
    def steps(self) -> list[Step]:
        """Get all steps in this route."""
        return list(self.__steps)

    def __hash__(self) -> int:
        """Get a hash of this route."""
        return hash(self.__steps)

    @classmethod
    def parse(cls, route: RouteLike) -> list[Step]:
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
            patched_route: list[Step] = []
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
    def decompose(cls, route: str) -> list[Step]:  # noqa: C901,PLR0915,PLR0912
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

        token_dot, token_escape, token_getitem_enter, token_getitem_exit = cls.TOKENS

        if not route.endswith(token_dot):
            route += token_dot

        key = ""
        getitem_entered: int | None = None
        getitem_was_exited: bool = False
        items: list[Step] = []
        push = items.append
        escape = False
        was_escaped = False
        step: Step

        for index, token in enumerate(route):
            if escape:
                key += token
                escape = False
                was_escaped = True
                continue
            is_last = index == len(route) - 1
            if token == token_dot:
                if getitem_entered is not None:
                    key += token
                else:
                    if getitem_was_exited:
                        step = GetItem(key, ignore_digit=was_escaped)
                        getitem_was_exited = False
                    else:
                        step = GetAttr(key)
                    was_escaped = False
                    push(step)
                    key = ""
            elif token == token_escape:
                if is_last:
                    key += token
                else:
                    escape = True
            elif token == token_getitem_enter:
                if getitem_entered is not None:
                    msg = (
                        f"Already seen {token_getitem_enter!r} "
                        f"that was not closed with {token_getitem_exit!r}"
                    )
                    raise RouteError(msg, route=route, index=index)
                if key or index == 0:
                    getitem_entered = index
                    if index:
                        if getitem_was_exited:
                            step = GetItem(key, ignore_digit=was_escaped)
                            getitem_was_exited = False
                        else:
                            step = GetAttr(key)
                        was_escaped = False
                        push(step)
                        key = ""
                else:
                    msg = f"No key between {route[index-1]!r} and {token!r}"
                    raise RouteError(msg, route=route, index=index)
            elif token == token_getitem_exit:
                if getitem_entered is None:
                    msg = f"{token!r} not preceded by {token_getitem_enter!r} token"
                    raise RouteError(msg, route=route, index=index)
                getitem_entered = None
                getitem_was_exited = True
            else:
                key += token
            if is_last and getitem_entered is not None:
                msg = f"Expected {token_getitem_exit!r} token"
                raise RouteError(msg, route=route, index=getitem_entered)
        return items

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

    def set(self, obj: Any, value: object, /) -> None:  # noqa: A003
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

    def __iter__(self) -> Iterator[Step]:
        """Yield all steps in this route."""
        yield from self.__steps

    def __repr__(self) -> str:
        """Represent this route in a string."""
        return f"<{type(self).__name__} {self.compose()!r}>"


EMPTY_ROUTE: Route = Route("", allow_empty=True)
