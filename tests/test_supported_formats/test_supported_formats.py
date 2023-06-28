from __future__ import annotations

from tests.test_supported_formats.conftest import TestedModel


def test_load_and_recreate(data_file, mock_data):
    model = TestedModel.load(data_file)
    model_dict = model.dict()
    assert model_dict == mock_data
    recreated = TestedModel.parse_obj(model_dict)
    assert recreated == model
    assert recreated.dict() == mock_data
