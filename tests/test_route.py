from __future__ import annotations

import pytest

from configzen.route import GetAttr, GetItem, Route, RouteError, Step


@pytest.mark.parametrize(
    "route_string, expected",
    [
        ("", []),
        ("foo", [GetAttr("foo")]),
        ("foo.bar", [GetAttr("foo"), GetAttr("bar")]),
        ("foo.bar[baz]", [GetAttr("foo"), GetAttr("bar"), GetItem("baz")]),
    ],
)
def test_route_decompose(route_string: str, expected: list[Step]) -> None:
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
