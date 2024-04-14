from __future__ import annotations
import sys

import pytest
from types import SimpleNamespace
from typing import TYPE_CHECKING

from configzen.configuration import BaseConfiguration
from configzen.module_proxy import ModuleProxy

if TYPE_CHECKING:
    from types import FrameType


class ConfigurationMock(BaseConfiguration):
    """Mock configuration class for testing."""

    field1: str = "default1"
    field2: str = "default2"


def test_init() -> None:
    proxy = ModuleProxy("test_module", ConfigurationMock())
    assert proxy.__configuration__ == ConfigurationMock()
    assert proxy.__locals__ == {}


@pytest.mark.parametrize("module_name", ["foo", "bar"])
def test_repr(module_name: str) -> None:
    proxy = ModuleProxy(module_name, ConfigurationMock())
    assert repr(proxy) == f"<configuration module {module_name!r}>"


def test_getattribute() -> None:
    obj = ConfigurationMock()
    proxy = ModuleProxy("test_module", obj)
    assert proxy.field1 == ConfigurationMock().field1
    obj.field1 = "value1"
    assert proxy.field1 == obj.field1


def test_setattr() -> None:
    proxy = ModuleProxy("test_module", ConfigurationMock())
    proxy.field1 = "value1"
    assert proxy.__locals__["field1"] == "value1"


def test_wrap_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module_name = "test_module"
    module_namespace = {
        "field1": "value1",
        "field2": "value2",
        "__name__": module_name,
        "__doc__": "Test Module",
        "__annotations__": {
            "field1": "str",
            "field2": "str",
        }
    }
    proxy = ModuleProxy.wrap_module(module_name, ConfigurationMock, module_namespace)
    assert proxy.__configuration__ == ConfigurationMock(
        field1=module_namespace["field1"],
        field2=module_namespace["field2"],
    )
    assert proxy.__locals__ == module_namespace
    assert proxy.field1 == module_namespace["field1"]
    assert proxy.field2 == module_namespace["field2"]


@pytest.mark.parametrize(
    "field1, field2, module_name",
    [
        ("foo", "bar", "test_module"),
        ("spam", "eggs", "test_module_name"),
    ],
)
def test_wrap_this_module(
    field1: str,
    field2: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def module_frame() -> FrameType:
        nonlocal field1, field2
        __name__ = module_name
        return sys._getframe(0)

    frame = module_frame()
    monkeypatch.setattr(sys, "_getframe", lambda _: SimpleNamespace(f_back=frame))
    proxy = ModuleProxy.wrap_this_module(ConfigurationMock)
    mock = ConfigurationMock(field1=field1, field2=field2)
    assert proxy.__configuration__ == mock
    assert proxy.__name__ == module_name
