from __future__ import annotations

import pydantic

from tests.test_supported_formats.conftest import TestedModel, SectionModel


def test_load_and_recreate(data_file, mock_data):
    try:
        model = TestedModel.load(data_file)
    except pydantic.ValidationError:
        # key-value pairs, little workaround
        assert data_file.suffix in (".shellvars", ".properties")
        model = TestedModel(main=SectionModel.load(data_file))
    model_dict = model.dict()
    assert model_dict == mock_data
    recreated = TestedModel.parse_obj(model_dict)
    assert recreated == model
    assert recreated.dict() == mock_data
