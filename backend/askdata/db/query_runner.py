"""SQLite helpers for agent execution and SQLAlchemy engine construction."""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

import cchardet
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from askdata.db.validator import SQLValidator


logger = logging.getLogger(__name__)

_validator = SQLValidator(dialect="sqlite")
_select_only = re.compile(r"^\s*(SELECT|WITH|UNION)\b", re.I)
_PREVIEW_ROW_LIMIT = 100
_CONFIDENCE_THRESHOLD = 0.6
_DEFAULT_SAMPLE_SIZE = 65536
_ENCODING_ALIASES = {
    "gb2312": "gbk",
    "gb-2312": "gbk",
    "gb18030": "gb18030",
    "big5": "big5",
}


def Execute(sql: str, database_path: str) -> dict:
    """Validate and execute a SELECT query against a SQLite database.

    Preserves the model's SQL and bounds the returned preview without rewriting LIMIT.
    Returns {"success": True, "sql": <executed_sql>, "columns": [...],
    "rows": [...], "truncated": bool}
    or {"success": False, "sql": <attempted_sql>, "error": "..."}.
    """
    cleaned = (sql or "").strip().rstrip(";")
    if not cleaned:
        return {"success": False, "sql": sql or "", "error": "SQL is empty"}

    validation = _validator.validate(cleaned)
    if not validation.is_valid:
        return {"success": False, "sql": cleaned, "error": validation.reason or "SQL validation failed"}

    normalized = validation.normalized_sql or cleaned
    if not _select_only.search(normalized):
        return {"success": False, "sql": normalized, "error": "Only SELECT / WITH / UNION queries are allowed"}

    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        return {"success": False, "sql": normalized, "error": f"SQLite database does not exist: {path}"}

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(normalized)
            columns = [item[0] for item in cursor.description or []]
            preview = cursor.fetchmany(_PREVIEW_ROW_LIMIT + 1)
            rows = [dict(row) for row in preview[:_PREVIEW_ROW_LIMIT]]
            return {
                "success": True,
                "sql": normalized,
                "columns": columns,
                "rows": rows,
                "truncated": len(preview) > _PREVIEW_ROW_LIMIT,
            }
        finally:
            connection.close()
    except Exception as exc:
        return {"success": False, "sql": normalized, "error": str(exc)}


def detect_file_encoding(file_path: str, sample_size: int = _DEFAULT_SAMPLE_SIZE) -> tuple[str, float]:
    """Sample a SQLite file and return the likely text encoding.

    This is a defensive helper for the SQLAlchemy executor path. The ReAct
    agent's `Execute()` path above still uses the standard sqlite3 read-only
    connection and preserves the model SQL without pagination rewrites.
    """
    try:
        with open(file_path, "rb") as handle:
            sample = handle.read(sample_size)
    except OSError as exc:
        logger.warning("Encoding detection failed for %s: %s; falling back to UTF-8", file_path, exc)
        return "utf-8", 0.0

    if not sample:
        return "utf-8", 1.0

    result = cchardet.detect(sample)
    encoding = (result.get("encoding") or "utf-8").lower()
    confidence = result.get("confidence") or 0.0
    encoding = _ENCODING_ALIASES.get(encoding, encoding)
    logger.info("Detected encoding for %s: %s (%.2f)", file_path, encoding, confidence)
    return encoding, confidence


def _build_resilient_text_factory(detected_encoding: Optional[str] = None):
    candidates = ["utf-8"]
    if detected_encoding and detected_encoding not in candidates:
        candidates.append(detected_encoding)
    if "gb18030" not in candidates:
        candidates.append("gb18030")

    def factory(raw: bytes):
        if raw is None:
            return None
        for encoding in candidates:
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    return factory


def _extract_sqlite_file_path(db_url: str) -> Optional[str]:
    if not db_url or not db_url.startswith("sqlite"):
        return None
    path_part = db_url.split("///", 1)[-1] if "///" in db_url else ""
    if not path_part or path_part == ":memory:":
        return None
    return path_part


def build_sqlite_engine(db_url: str, **create_engine_kwargs) -> Engine:
    """Build a SQLite SQLAlchemy engine with resilient text decoding.

    Used by `SQLExecutor`; kept separate from `Execute()` because the ReAct
    Text2SQL loop needs a lightweight runner that does not rewrite LIMIT.
    """
    connect_args = dict(create_engine_kwargs.pop("connect_args", None) or {})
    connect_args.setdefault("check_same_thread", False)

    engine = create_engine(db_url, connect_args=connect_args, **create_engine_kwargs)

    file_path = _extract_sqlite_file_path(db_url)
    detected_encoding = None
    if file_path:
        encoding, confidence = detect_file_encoding(file_path)
        if confidence >= _CONFIDENCE_THRESHOLD and encoding != "utf-8":
            detected_encoding = encoding

    text_factory = _build_resilient_text_factory(detected_encoding)

    @event.listens_for(engine, "connect")
    def _set_text_factory(dbapi_conn, conn_record):  # noqa: ANN001
        dbapi_conn.text_factory = text_factory

    return engine
