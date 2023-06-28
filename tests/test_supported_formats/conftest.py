import io
import json
from typing import Union

import configparser
import msgpack
import pytest
import yaml
import bson
import cbor2
import cbor
import toml
from amazon.ion import simpleion as ion

from configzen import ConfigModel
from tests.conftest import testpath

file_dir = testpath("test_supported_formats/data")
file_dir.mkdir(exist_ok=True)


class IncorporatedModel(ConfigModel):
    string: str


class SectionModel(ConfigModel):
    integer: int
    incorporated_model: IncorporatedModel
    union_value: Union[bool, float]
    collection_value: list[str]
    dict_value: dict[str, str]


class TestedModel(ConfigModel):
    main: SectionModel


def ini_compose(data):
    config = configparser.ConfigParser()
    config.read_dict(data)
    fp = io.StringIO()
    config.write(fp)
    return fp.getvalue()


composer = {
    "ini": ini_compose,
    "json": json.dumps,
    "yaml": yaml.dump,
    "toml": toml.dumps,
    "ion": ion.dumps,
    "bson": bson.encode,
    "cbor2": cbor2.dumps,
    "cbor": cbor.dumps,
    "msgpack": msgpack.dumps,
}


@pytest.fixture(
    name="mock_data",
    scope="session",
    params=[
        {
            "main": {
                "integer": 0xdeadbeef,
                "incorporated_model": {"string": "Hello world!"},
                "union_value": 0.2137,
                "collection_value": ["this", "is", "configzen"],
                "dict_value": {"configzen": "is this"}
            }
        }
    ]
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
        # (composer["ion"], file_dir / "data.ion", True),
        # (composer["msgpack"], file_dir / "data.msgpack", True),
    ],
)
def data_file(request, mock_data):
    dumper, dest_file, uses_binary_data = request.param
    dest_file.touch()
    if uses_binary_data:
        dest_file.write_bytes(dumper(mock_data))
    else:
        dest_file.write_text(dumper(mock_data))
    yield dest_file
    # dest_file.unlink()
