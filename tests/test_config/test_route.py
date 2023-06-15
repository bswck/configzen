from __future__ import annotations

import pytest

from configzen.config import ConfigRoute
from configzen.errors import ConfigSyntaxError

STRING_DECOMPOSITION_PARAMS = [
    ("a.b.c", ["a", "b", "c"]),
    (r"a\.b.c", ["a.b", "c"]),
    ("a.b.[c.d]", ["a", "b", "c.d"]),
    ("[a.b].c.[d.e]", ["a.b", "c", "d.e"]),
    (r"a.[b.[c.d]\.e].f", ["a", "b.[c.d].e", "f"]),
    (r"[a.b][c.d]", ["a.b][c.d"]),
]


@pytest.mark.parametrize(
    "obj, expected",
    [
        # List inputs
        (["a", "b", "c"], ["a", "b", "c"]),
        (["a", "b", "c.d"], ["a", "b", "c.d"]),
        (["a.b", "c", "d.e"], ["a.b", "c", "d.e"]),

        # Route inputs
        (ConfigRoute(["a", "b", "c"]), ["a", "b", "c"]),
        (ConfigRoute(["a", "b", "c.d"]), ["a", "b", "c.d"]),
        (ConfigRoute(["a.b", "c", "d.e"]), ["a.b", "c", "d.e"]),

        # String inputs
        *STRING_DECOMPOSITION_PARAMS
    ]
)
def test_parse(obj, expected):
    assert ConfigRoute.parse(obj) == expected


@pytest.mark.parametrize(
    "composed, decomposed",
    STRING_DECOMPOSITION_PARAMS
)
def test_decompose(composed, decomposed):
    assert ConfigRoute.decompose(composed) == decomposed


@pytest.mark.parametrize(
    "illegal_input",
    [
        # String inputs
        "a.b.[c.d",
        "a.b.c]",
        "[a.b.c",
    ]
)
def test_illegal_inputs(illegal_input):
    with pytest.raises(ConfigSyntaxError):
        ConfigRoute(illegal_input)


@pytest.mark.parametrize(
    "route, expected",
    [
        (ConfigRoute("a.b.c"), "a.b.c"),
        (ConfigRoute("a.[b.c]"), "a.[b.c]"),
        (ConfigRoute(r"a.b\.c"), "a.[b.c]"),
        (ConfigRoute(r"a.[b.[c.d]\.e].f"), r"a.[b.[c.d]\.e].f"),
        (ConfigRoute(r"a.b\.\[c\.d\]\.e.f"), r"a.[b.[c.d]\.e].f"),
    ]
)
def test_compose(route, expected):
    assert route.compose() == expected


def test_enter():
    assert ConfigRoute("a").enter("b") == ConfigRoute("a.b")
    assert ConfigRoute("a").enter(["b", "c"]) == ConfigRoute("a.b.c")
    assert ConfigRoute("a").enter(ConfigRoute("b.c")) == ConfigRoute("a.b.c")
    assert ConfigRoute("a").enter(ConfigRoute(["b", "c"])) == ConfigRoute("a.b.c")
    assert ConfigRoute("a").enter(ConfigRoute("b.[c.d]")) == ConfigRoute("a.b.[c.d]")


def test_equality_operator():
    assert ConfigRoute("a.b.c") == ConfigRoute("a.b.c")
    assert ConfigRoute("a.b.c") == ["a", "b", "c"]
    assert ConfigRoute(["a", "b", "c"]) == ["a", "b", "c"]
