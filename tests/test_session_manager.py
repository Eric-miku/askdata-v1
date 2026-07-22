import asyncio
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.api.session_manager import SessionManager  # noqa: E402


def test_session_history_is_persisted_across_manager_instances(tmp_path):
    async def exercise() -> None:
        first = SessionManager(checkpoint_dir=str(tmp_path))
        session_id = await first.create_session("demo")
        assert await first.append_history(session_id, "How many?", "SELECT COUNT(*) FROM items", "3")

        second = SessionManager(checkpoint_dir=str(tmp_path))
        restored = await second.get_session(session_id)

        assert restored is not None
        assert restored["thread_id"] == session_id
        assert restored["database_id"] == "demo"
        assert restored["history"] == [{
            "question": "How many?",
            "sql": "SELECT COUNT(*) FROM items",
            "answer": "3",
            "timestamp": restored["history"][0]["timestamp"],
        }]
        sessions, total = await second.list_sessions()
        assert total == 1
        assert sessions[0]["question_count"] == 1

        first.save_agent_state(session_id, {"database_id": "demo", "sql": "SELECT COUNT(*) FROM items"})
        assert second.load_agent_state(session_id) == {
            "database_id": "demo",
            "sql": "SELECT COUNT(*) FROM items",
        }

    asyncio.run(exercise())


def test_sessions_are_isolated_by_user_and_legacy_database_is_migrated(tmp_path):
    metadata_path = tmp_path / "sessions.sqlite"
    with sqlite3.connect(metadata_path) as connection:
        connection.execute(
            """CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, database_id TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE session_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                question TEXT NOT NULL, sql TEXT, answer TEXT NOT NULL, timestamp REAL NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO sessions VALUES ('legacy', 'legacy', 1, 1, 'demo')"
        )

    async def exercise() -> None:
        manager = SessionManager(checkpoint_dir=str(tmp_path))
        assert await manager.get_session("legacy", "local-user") is not None
        assert await manager.get_session("legacy", "alice") is None

        alice_session = await manager.create_session("sales", "alice")
        bob_session = await manager.create_session("sales", "bob")
        assert await manager.append_history(alice_session, "Alice question", user_id="alice")
        assert not await manager.append_history(alice_session, "Bob intrusion", user_id="bob")

        alice_sessions, alice_total = await manager.list_sessions(user_id="alice")
        bob_sessions, bob_total = await manager.list_sessions(user_id="bob")
        assert alice_total == bob_total == 1
        assert [item["session_id"] for item in alice_sessions] == [alice_session]
        assert [item["session_id"] for item in bob_sessions] == [bob_session]
        assert await manager.get_history(alice_session, "alice") is not None
        assert await manager.get_history(alice_session, "bob") is None
        assert not await manager.delete_session(alice_session, "bob")
        assert await manager.delete_session(alice_session, "alice")

    asyncio.run(exercise())
