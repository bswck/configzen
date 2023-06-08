import io
import os
import sys
import tempfile

import pytest
from configzen import ConfigModel, ConfigMeta, ConfigAgent
from configzen.config import get_context


class Model(ConfigModel):
    item: int


class ModelWithHeader(ConfigModel):
    header: Model


default_params = pytest.mark.parametrize(
    "blob, ac_parser, expected",
    [
        ("{\"item\": 123}", "json", Model(item=123)),
        ("item: 456", "yaml", Model(item=456)),
        ("[header]\nitem = 789", "toml", ModelWithHeader(header=Model(item=789))),
        ("[header]\nitem = 101", "ini", ModelWithHeader(header=Model(item=101))),
    ]
)


@default_params
def test_load_stream(blob, ac_parser, expected):
    loaded_model: type[ConfigModel] = type(expected)
    assert loaded_model.load(
        ConfigAgent(resource=io.StringIO(blob), ac_parser=ac_parser)
    ) == expected

    loaded_model.__config__.resource = io.StringIO(blob)
    loaded_model.__config__.ac_parser = ac_parser
    assert loaded_model.load() == expected

    loaded_model.__config__.resource = ConfigAgent(
        resource=io.StringIO(blob),
        ac_parser=ac_parser
    )
    loaded_model.__config__.ac_parser = None
    assert loaded_model.load() == expected


def get_temp_file(blob):
    file = tempfile.NamedTemporaryFile(
        mode="w+",
        encoding=sys.getdefaultencoding(),
        delete=False
    )
    file.write(blob)
    file.close()
    return file


@default_params
def test_load_file(blob, ac_parser, expected):
    loaded_model: type[ConfigModel] = type(expected)

    file = get_temp_file(blob)
    assert loaded_model.load(
        ConfigAgent(resource=file.name, ac_parser=ac_parser)
    ) == expected
    os.unlink(file.name)

    file = get_temp_file(blob)
    loaded_model.__config__.resource = file.name
    loaded_model.__config__.ac_parser = ac_parser
    assert loaded_model.load() == expected
    os.unlink(file.name)

    file = get_temp_file(blob)
    loaded_model.__config__.resource = ConfigAgent(
        resource=file.name,
        ac_parser=ac_parser
    )
    loaded_model.__config__.ac_parser = None
    assert loaded_model.load() == expected
    os.unlink(file.name)
