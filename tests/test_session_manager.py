import asyncio
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
