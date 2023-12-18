from __future__ import annotations

from typing import TYPE_CHECKING, List

import pytest

from configzen.configuration import BaseConfiguration
from configzen.errors import LinkedRouteError, RouteError
from configzen.routes import GetAttr, GetItem, LinkedRoute, Route, Step

if TYPE_CHECKING:
    from typing import Any


@pytest.mark.parametrize(
    "route_string, expected",
    [
        ("", []),
        ("foo", [GetAttr("foo")]),
        ("foo.bar", [GetAttr("foo"), GetAttr("bar")]),
        ("foo.bar[baz]", [GetAttr("foo"), GetAttr("bar"), GetItem("baz")]),
    ],
)
def test_route_decompose(route_string: str, expected: list[Step[Any]]) -> None:
    assert Route.decompose(route_string) == expected


@pytest.mark.parametrize(
    "route, expected",
    [
        (Route("foo.bar[baz]"), "foo.bar[baz]"),
        (Route("foo.bar[0][baz\\.bar]"), "foo.bar[0][baz\\.bar]"),
    ],
)
def test_route_compose(route: Route, expected: str) -> None:
    assert route.compose() == expected


def test_linked_route() -> None:
    class Bar(BaseConfiguration):
        baz: str

    class Foo(BaseConfiguration):
        bar: Bar
        biz: List[Bar]

    assert Foo.bar.baz == LinkedRoute(Foo, "bar.baz")
    assert Foo.biz == LinkedRoute(Foo, "biz")
    assert Foo.biz[0] == LinkedRoute(Foo, "biz[0]")
    assert Foo.biz[0].baz == LinkedRoute(Foo, "biz[0].baz")

    with pytest.raises(LinkedRouteError):
        assert Foo.bar.xaz  # type: ignore[attr-defined]
