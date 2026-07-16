from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.core.config import Settings


def test_settings_resolves_milvus_uri_from_legacy_host_and_port():
    settings = Settings(
        _env_file=None,
        MILVUS_URI="",
        MILVUS_HOST="7.59.11.153",
        MILVUS_PORT=19530,
    )

    assert settings.ResolvedMilvusUri() == "http://7.59.11.153:19530"


def test_settings_prefers_explicit_milvus_uri_over_legacy_host_and_port():
    settings = Settings(
        _env_file=None,
        MILVUS_URI="http://milvus.internal:19530",
        MILVUS_HOST="7.59.11.153",
        MILVUS_PORT=19530,
    )

    assert settings.ResolvedMilvusUri() == "http://milvus.internal:19530"
