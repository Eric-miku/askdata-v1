from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.tools.embedding_client import EmbeddingClient, EmbeddingConfigurationError


class FakeEmbeddingsApi:
    def __init__(self, data, model=None):
        self.data = data
        self.model = model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=self.data, model=self.model)


def item(index, embedding):
    return SimpleNamespace(index=index, embedding=embedding)


def test_embedding_client_preserves_response_order_by_index():
    api = FakeEmbeddingsApi([item(1, [3.0, 4.0]), item(0, [1.0, 2.0])])
    client = EmbeddingClient(api=api, model="test-model", dimension=2)

    assert client.Embed(["first", "second"]) == [[1.0, 2.0], [3.0, 4.0]]
    assert api.calls == [{"model": "test-model", "input": ["first", "second"]}]


@pytest.mark.parametrize(
    "data, message",
    [
        ([item(0, [0.1, 0.2])], "returned 1 vectors for 2 texts"),
        ([item(0, [0.1]), item(1, [0.2, 0.3])], "expected 2"),
        ([item(0, [0.1, 0.2]), item(0, [0.3, 0.4])], "indices"),
        ([item(None, [0.1, 0.2]), item(1, [0.3, 0.4])], "indices"),
    ],
)
def test_embedding_client_strictly_validates_count_dimension_and_indices(data, message):
    client = EmbeddingClient(api=FakeEmbeddingsApi(data), model="test-model", dimension=2)

    with pytest.raises(EmbeddingConfigurationError, match=message):
        client.Embed(["first", "second"])


def test_embedding_client_does_not_call_api_for_empty_input():
    api = FakeEmbeddingsApi([])
    client = EmbeddingClient(api=api, model="test-model", dimension=2)

    assert client.Embed([]) == []
    assert api.calls == []


def test_embedding_client_rejects_a_different_returned_model():
    api = FakeEmbeddingsApi([item(0, [1.0, 2.0])], model="unexpected-model")
    client = EmbeddingClient(api=api, model="configured-model", dimension=2)

    with pytest.raises(EmbeddingConfigurationError, match="configured-model"):
        client.Embed(["schema"])
