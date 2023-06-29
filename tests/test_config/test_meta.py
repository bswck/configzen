from __future__ import annotations

import io

import pytest

from pydantic import ConfigError
from configzen import ConfigModel, ConfigAgent, ConfigMeta


class NoAutoUpdateForwardRefs(ConfigModel):
    item: Item

    class Config(ConfigMeta):
        autoupdate_forward_refs = False


class AutoUpdateForwardRefs(ConfigModel):
    item: Item


class Item(ConfigModel):
    foo: int


def get_agent():
    resource = io.StringIO("item:\n  foo: 123")
    agent = ConfigAgent(resource=resource, parser_name="yaml")
    return agent


def test_autoupdate_forward_refs():
    assert (
        AutoUpdateForwardRefs.load(get_agent())
        == AutoUpdateForwardRefs(item=Item(foo=123))
    )

    with pytest.raises(ConfigError):
        NoAutoUpdateForwardRefs.load(get_agent())


def test_resource_and_parser_name():
    class FixedResourceModel(ConfigModel):
        class Config(ConfigMeta):
            resource = io.StringIO("foo: 123")
            parser_name = "yaml"

        foo: int

    assert FixedResourceModel.load() == FixedResourceModel(foo=123)

    overridden_resource = ConfigAgent(io.StringIO("foo: 456"), parser_name="yaml")
    assert FixedResourceModel.load(overridden_resource) == FixedResourceModel(foo=456)
