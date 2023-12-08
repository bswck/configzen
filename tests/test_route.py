from __future__ import annotations
import pytest
from configzen.route import Route, RouteError, Step, GetItem, GetAttr


@pytest.mark.parametrize(
    "route, expected",
    [
        ("", []),
        ("foo", [GetAttr("foo")]),
        ("foo.bar", [GetAttr("foo"), GetAttr("bar")]),
        ("foo.bar[baz]", [GetAttr("foo"), GetAttr("bar"), GetItem("baz")]),
    ],
)
def test_route_decompose(route: str, expected: list[Step]) -> None:
    assert Route.decompose(route) == expected


def test_route_compose() -> None:
    pass
