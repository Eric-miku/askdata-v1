import sqlite3

from askdata.db.optimizer import ExplainSqliteQuery


def _database(tmp_path):
    path = tmp_path / "optimizer.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                region TEXT NOT NULL,
                amount REAL NOT NULL
            );
            INSERT INTO orders(customer_id, region, amount) VALUES
                (1, '华东', 100), (2, '华南', 200);
        """)
    return path


def test_explain_returns_plan_and_manual_index_candidate(tmp_path):
    result = ExplainSqliteQuery(
        "SELECT customer_id, SUM(amount) FROM orders WHERE region = '华东' GROUP BY customer_id",
        _database(tmp_path),
    )
    assert result["success"] is True
    assert result["plan"]
    candidate = next(item for item in result["suggestions"] if item["type"] == "index_candidate")
    assert candidate["table"] == "orders"
    assert candidate["columns"] == ["region"]
    assert candidate["automatic"] is False
    assert "CREATE INDEX" in candidate["sql"]
    assert result["warnings"]


def test_explain_does_not_repeat_existing_index(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE INDEX idx_orders_region ON orders(region)")
    result = ExplainSqliteQuery("SELECT * FROM orders WHERE region = '华东'", path)
    assert result["success"] is True
    assert all(item.get("columns") != ["region"] for item in result["suggestions"])


def test_explain_blocks_mutation_and_multi_statement(tmp_path):
    path = _database(tmp_path)
    for sql in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders"):
        result = ExplainSqliteQuery(sql, path)
        assert result["success"] is False
        assert result["error_code"] == "SQL_BLOCKED"

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
