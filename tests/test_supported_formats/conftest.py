import configparser
import functools
import io
import json
import os
from typing import Union

import bson
import cbor
import cbor2
import configobj
import msgpack
import pytest
import toml
import yaml
from amazon.ion import simpleion as ion
from anyconfig.backend import properties

from configzen import ConfigModel
from tests.conftest import testpath

file_dir = testpath("test_supported_formats/data")
file_dir.mkdir(exist_ok=True)


class IncorporatedModel(ConfigModel):
    string: str


class SectionModel(ConfigModel):
    integer: int
    incorporated_model: IncorporatedModel = IncorporatedModel(string="Hello world!")
    union_value: Union[bool, float]
    collection_value: list[str]
    dict_value: dict[str, str] = {"configzen": "is this"}


class MockModel(ConfigModel):
    main: SectionModel


def ini_compose(data):
    config = configparser.ConfigParser()
    config.read_dict(data)
    fp = io.StringIO()
    config.write(fp)
    return fp.getvalue()


def configobj_compose(data):
    config = configobj.ConfigObj()
    config.merge(data)
    fp = io.BytesIO()
    config.write(fp)
    return fp.getvalue().decode()


def shellvars_compose(data):
    output = ""
    for key, value in data.items():
        output += f"{key}='{value}'{os.linesep}"
    return output


def properties_compose(data):
    output = ""
    for key, value in data.items():
        output += f"{key}={properties.escape(str(value))}{os.linesep}"
    return output


composer = {
    "ini": ini_compose,
    "configobj": configobj_compose,
    "json": functools.partial(json.dumps, indent=4),
    "yaml": yaml.dump,
    "toml": toml.dumps,
    "ion": ion.dumps,
    "bson": bson.encode,
    "cbor2": cbor2.dumps,
    "cbor": cbor.dumps,
    "msgpack": msgpack.dumps,
    "shellvars": shellvars_compose,
    "properties": properties_compose,
}


@pytest.fixture(
    name="mock_data",
    scope="session",
    params=[
        {
            "main": {
                "integer": 0xDEADBEEF,
                "incorporated_model": {"string": "Hello world!"},
                "union_value": 0.2137,
                "collection_value": ["this", "is", "configzen"],
                "dict_value": {"configzen": "is this"},
            }
        }
    ],
)
def mock_data_fixture(request):
    yield request.param


@pytest.fixture(
    scope="session",
    autouse=True,
    params=[
        (composer["ini"], file_dir / "data.ini", False),
        (composer["json"], file_dir / "data.json", False),
        (composer["yaml"], file_dir / "data.yaml", False),
        (composer["toml"], file_dir / "data.toml", False),
        (composer["bson"], file_dir / "data.bson", True),
        (composer["cbor2"], file_dir / "data.cbor2", True),
        (composer["cbor"], file_dir / "data.cbor", True),
        (composer["shellvars"], file_dir / "data.shellvars", False),
        (composer["properties"], file_dir / "data.properties", False),
        (composer["ion"], file_dir / "data.ion", True),
        (composer["configobj"], file_dir / "data.configobj", False),
        # (composer["msgpack"], file_dir / "data.msgpack", True),
    ],
)
def data_file(request, mock_data):
    dumper, dest_file, uses_binary_data = request.param

    if dumper in (composer["shellvars"], composer["properties"]):
        used_mock_data = mock_data["main"].copy()
        mock_data = used_mock_data

    dest_file.touch()
    if uses_binary_data:
        dest_file.write_bytes(dumper(mock_data))
    else:
        dest_file.write_text(dumper(mock_data))
    yield dest_file
