import asyncio
import json
from datetime import datetime, timezone

import pytest

from askdata.api.session_store import SessionStore


def assert_iso_utc(value: str) -> None:
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    assert parsed.isoformat() == value


def answer_turn(turn_id: str, question: str = "How many schools?") -> dict:
    return {
        "id": turn_id,
        "question": question,
        "response_kind": "answer",
        "answer": "There are 3 schools.",
        "sql": "SELECT COUNT(*) AS count FROM schools",
        "result_preview": [{"count": 3}],
        "chart": {"type": "vertical_bar", "value_fields": ["count"]},
        "confidence": "high",
        "error": None,
        "trace": [{"sequence": 1, "step": "ExecuteSql", "status": "success"}],
    }


@pytest.mark.asyncio
async def test_sessions_and_turns_survive_store_restart(tmp_path):
    path = tmp_path / "nested" / "sessions.sqlite"
    first = SessionStore(path)
    await first.Initialize()
    session_id = await first.CreateSession("california_schools", "School count")
    await first.SaveTurn(session_id, answer_turn("turn-1"))
    await first.Close()

    second = SessionStore(path)
    await second.Initialize()
    session = await second.GetSession(session_id)
    await second.Close()

    assert session is not None
    assert session["database_id"] == "california_schools"
    assert session["title"] == "School count"
    assert session["turns"] == [
        {
            **answer_turn("turn-1"),
            "created_at": session["turns"][0]["created_at"],
            "clarification": None,
        }
    ]
    assert_iso_utc(session["created_at"])
    assert_iso_utc(session["updated_at"])
    assert_iso_utc(session["turns"][0]["created_at"])


@pytest.mark.asyncio
async def test_delete_session_cascades_turns_and_clarifications(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    session_id = await store.CreateSession("db")
    await store.SaveTurn(session_id, answer_turn("turn-1"))
    clarification = await store.CreateClarification(
        "turn-1", "Which year?", [{"id": "2024", "label": "2024"}]
    )

    assert await store.DeleteSession(session_id) is True
    assert await store.DeleteSession(session_id) is False
    assert await store.GetSession(session_id) is None
    turns = await (await store._connection.execute("SELECT id FROM turns")).fetchall()
    clarifications = await (
        await store._connection.execute("SELECT id FROM clarifications")
    ).fetchall()
    await store.Close()

    assert clarification["status"] == "pending"
    assert turns == []
    assert clarifications == []


@pytest.mark.asyncio
async def test_list_sessions_orders_by_updated_at_descending_and_limits(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    first = await store.CreateSession("db", "First")
    second = await store.CreateSession("db", "Second")
    await store.SaveTurn(first, answer_turn("turn-1"))

    sessions = await store.ListSessions(limit=1)

    assert [session["id"] for session in sessions] == [first]
    assert sessions[0]["title"] == "First"
    assert await store.ListSessions(limit=2) == sorted(
        await store.ListSessions(limit=2),
        key=lambda session: session["updated_at"],
        reverse=True,
    )
    assert second != first
    with pytest.raises(ValueError, match="limit must be greater than 0"):
        await store.ListSessions(0)
    await store.Close()


@pytest.mark.asyncio
async def test_save_turn_updates_session_timestamp_and_upserts_same_turn(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    session_id = await store.CreateSession("db")
    before = (await store.GetSession(session_id))["updated_at"]

    await store.SaveTurn(session_id, answer_turn("turn-1"))
    first = await store.GetSession(session_id)
    updated = answer_turn("turn-1", "Resolved question")
    updated["answer"] = "Updated answer"
    await store.SaveTurn(session_id, updated)
    after = await store.GetSession(session_id)

    assert first["updated_at"] > before
    assert after["updated_at"] > first["updated_at"]
    assert len(after["turns"]) == 1
    assert after["turns"][0]["question"] == "Resolved question"
    assert after["turns"][0]["answer"] == "Updated answer"
    with pytest.raises(ValueError, match="Session does not exist"):
        await store.SaveTurn("missing-session", answer_turn("turn-2"))
    assert store._connection.in_transaction is False
    await store.Close()


@pytest.mark.asyncio
async def test_clarification_state_persists_and_resolution_is_session_scoped(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    await store.Initialize()
    owner_session = await store.CreateSession("db")
    other_session = await store.CreateSession("db")
    await store.SaveTurn(owner_session, answer_turn("turn-1"))
    options = [
        {"id": "recent", "label": "Most recent year", "metadata": {"year": 2024}},
        {"id": "all", "label": "All years"},
    ]
    pending = await store.CreateClarification("turn-1", "Which period?", options)

    assert pending["status"] == "pending"
    assert pending["options"] == options
    assert pending["resolution"] is None
    assert pending["resolved_at"] is None
    assert_iso_utc(pending["created_at"])
    stored_pending = (await store.GetSession(owner_session))["turns"][0][
        "clarification"
    ]
    assert stored_pending == pending
    assert (
        await store.ResolveClarification(
            other_session, pending["id"], {"option_id": "recent"}
        )
        is None
    )
    resolution = {"option_id": "recent", "context": {"year": 2024}}
    resolved = await store.ResolveClarification(owner_session, pending["id"], resolution)
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == resolution
    assert_iso_utc(resolved["resolved_at"])
    assert await store.ResolveClarification(owner_session, pending["id"], resolution) is None
    assert await store.ResolveClarification(owner_session, "missing", resolution) is None
    await store.Close()

    reopened = SessionStore(path)
    await reopened.Initialize()
    session = await reopened.GetSession(owner_session)
    assert session["turns"][0]["clarification"] == resolved
    with pytest.raises(ValueError, match="Turn does not exist"):
        await reopened.CreateClarification("missing-turn", "Prompt", [])
    assert reopened._connection.in_transaction is False
    await reopened.Close()


@pytest.mark.asyncio
async def test_store_pragmas_json_round_trip_and_deterministic_storage(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    session_id = await store.CreateSession("db")
    turn = answer_turn("turn-1")
    turn["error"] = {"retryable": False, "details": ["bad input"]}
    await store.SaveTurn(session_id, turn)
    clarification = await store.CreateClarification(
        "turn-1", "Choose", [{"label": "B", "id": "b", "data": {"z": 1, "a": 2}}]
    )

    foreign_keys = await (await store._connection.execute("PRAGMA foreign_keys")).fetchone()
    busy_timeout = await (await store._connection.execute("PRAGMA busy_timeout")).fetchone()
    journal_mode = await (await store._connection.execute("PRAGMA journal_mode")).fetchone()
    raw_turn = await (
        await store._connection.execute(
            "SELECT result_preview_json, chart_json, error_json, trace_json FROM turns"
        )
    ).fetchone()
    raw_options = await (
        await store._connection.execute("SELECT options_json FROM clarifications")
    ).fetchone()
    session = await store.GetSession(session_id)
    await store.Close()

    assert foreign_keys[0] == 1
    assert busy_timeout[0] == 5000
    assert journal_mode[0].lower() == "wal"
    assert raw_turn[0] == json.dumps(turn["result_preview"], sort_keys=True, separators=(",", ":"))
    assert raw_turn[1] == json.dumps(turn["chart"], sort_keys=True, separators=(",", ":"))
    assert raw_turn[2] == json.dumps(turn["error"], sort_keys=True, separators=(",", ":"))
    assert raw_turn[3] == json.dumps(turn["trace"], sort_keys=True, separators=(",", ":"))
    assert raw_options[0] == json.dumps(
        clarification["options"], sort_keys=True, separators=(",", ":")
    )
    assert session["turns"][0]["result_preview"] == turn["result_preview"]
    assert session["turns"][0]["chart"] == turn["chart"]
    assert session["turns"][0]["error"] == turn["error"]
    assert session["turns"][0]["trace"] == turn["trace"]


@pytest.mark.asyncio
async def test_methods_require_initialize(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    calls = [
        lambda: store.CreateSession("db"),
        lambda: store.ListSessions(),
        lambda: store.ListSessions(0),
        lambda: store.GetSession("session"),
        lambda: store.DeleteSession("session"),
        lambda: store.SaveTurn("session", answer_turn("turn")),
        lambda: store.SaveTurn("session", {}),
        lambda: store.CreateClarification("turn", "Prompt", []),
        lambda: store.ResolveClarification("session", "clarification", {}),
        lambda: store.Close(),
    ]
    for call in calls:
        with pytest.raises(RuntimeError, match="not initialized"):
            await call()


@pytest.mark.asyncio
async def test_reads_wait_for_an_in_flight_write_transaction(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions.sqlite")
    await store.Initialize()
    session_id = await store.CreateSession("db")
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    original_commit = store._connection.commit

    async def delayed_commit():
        commit_started.set()
        await release_commit.wait()
        await original_commit()

    monkeypatch.setattr(store._connection, "commit", delayed_commit)
    write_task = asyncio.create_task(
        store.SaveTurn(session_id, answer_turn("turn-1"))
    )
    await commit_started.wait()
    read_task = asyncio.create_task(store.GetSession(session_id))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(read_task), timeout=0.05)
    finally:
        release_commit.set()
        await write_task

    session = await read_task
    assert [turn["id"] for turn in session["turns"]] == ["turn-1"]
    monkeypatch.setattr(store._connection, "commit", original_commit)
    await store.Close()


@pytest.mark.asyncio
async def test_two_stores_can_save_turns_concurrently(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    first = SessionStore(path)
    second = SessionStore(path)
    await first.Initialize()
    await second.Initialize()
    session_id = await first.CreateSession("db")

    begin_count = 0
    begin_count_lock = asyncio.Lock()
    both_begun = asyncio.Event()
    first_has_write_reservation = asyncio.Event()
    second_attempted_write = asyncio.Event()

    async def wait_for_both_begins():
        nonlocal begin_count
        async with begin_count_lock:
            begin_count += 1
            if begin_count == 2:
                both_begun.set()
        await both_begun.wait()

    def coordinate_connection(store, position):
        original_execute = store._connection.execute
        transaction_mode = ""

        async def coordinated_execute(sql, parameters=()):
            nonlocal transaction_mode
            normalized = " ".join(sql.split()).upper()
            if normalized in {"BEGIN", "BEGIN IMMEDIATE"}:
                transaction_mode = normalized
                await wait_for_both_begins()
                return await original_execute(sql, parameters)
            if normalized.startswith("INSERT INTO TURNS") and transaction_mode == "BEGIN":
                if position == "first":
                    cursor = await original_execute(sql, parameters)
                    first_has_write_reservation.set()
                    await second_attempted_write.wait()
                    return cursor
                await first_has_write_reservation.wait()
                second_attempted_write.set()
            return await original_execute(sql, parameters)

        monkeypatch.setattr(store._connection, "execute", coordinated_execute)
        return original_execute

    first_execute = coordinate_connection(first, "first")
    second_execute = coordinate_connection(second, "second")
    results = await asyncio.gather(
        first.SaveTurn(session_id, answer_turn("turn-1")),
        second.SaveTurn(session_id, answer_turn("turn-2")),
        return_exceptions=True,
    )
    monkeypatch.setattr(first._connection, "execute", first_execute)
    monkeypatch.setattr(second._connection, "execute", second_execute)

    assert results == ["turn-1", "turn-2"]
    session = await first.GetSession(session_id)
    assert {turn["id"] for turn in session["turns"]} == {"turn-1", "turn-2"}
    await first.Close()
    await second.Close()
