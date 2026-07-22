from pathlib import Path
import sqlite3
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.db.query_runner import Execute  # noqa: E402


def test_execute_rejects_missing_database_without_creating_file(tmp_path):
    database_path = tmp_path / "missing.sqlite"

    result = Execute("SELECT 1", str(database_path))

    assert result["success"] is False
    assert "does not exist" in result["error"]
    assert not database_path.exists()


def test_execute_decodes_legacy_gbk_text_without_crashing(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE items(note TEXT)")
    connection.execute("INSERT INTO items(note) VALUES (CAST(X'C4E3BAC3' AS TEXT))")
    connection.commit()
    connection.close()

    result = Execute("SELECT note FROM items", str(database_path))

    assert result["success"] is True
    assert result["rows"] == [{"note": "你好"}]
