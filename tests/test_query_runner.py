from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.db.query_runner import Execute  # noqa: E402


def test_execute_rejects_missing_database_without_creating_file(tmp_path):
    database_path = tmp_path / "missing.sqlite"

    result = Execute("SELECT 1", str(database_path))

    assert result["success"] is False
    assert "does not exist" in result["error"]
    assert not database_path.exists()
