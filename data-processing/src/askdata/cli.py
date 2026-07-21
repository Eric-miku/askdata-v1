from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any


DEFAULT_RAW_DIR = Path("data/bird/raw/minidev")
DEFAULT_DB_DIR = Path("data/bird/databases")
DEFAULT_OUT_DIR = Path("data/bird/processed")


@dataclass(frozen=True)
class PreparedPaths:
    raw_dir: Path
    minidev_dir: Path
    db_dir: Path
    out_dir: Path
    schemas_dir: Path
    schema_prompts_dir: Path
    demo_dir: Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="askdata")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare-bird",
        help="Prepare BIRD Mini-Dev files into normalized JSON/JSONL assets.",
    )
    prepare.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    prepare.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    prepare.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    prepare.add_argument("--demo-db-limit", type=int, default=10)
    prepare.add_argument("--demo-question-limit", type=int, default=50)
    prepare.add_argument("--split", default="demo", choices=["demo", "dev", "train"])
    prepare.add_argument("--force", action="store_true", help="Overwrite existing processed outputs.")
    prepare.add_argument("--validate-sql", action="store_true", help="Execute gold SQL and record pass/fail.")
    prepare.add_argument("--build-cache", action="store_true", help="Persist gold SQL execution result cache.")
    prepare.add_argument("--max-rows", type=int, default=200, help="Maximum cached rows per query.")
    prepare.add_argument("--build-embeddings", action="store_true", help="Build schema embedding vector index outputs.")
    prepare.add_argument(
        "--embedding-provider",
        default=os.getenv("EMBEDDING_PROVIDER", "openai-compatible"),
        choices=["openai-compatible", "hash"],
        help="Embedding provider. Use hash only for local contract tests.",
    )
    prepare.add_argument(
        "--embedding-api-base",
        default=os.getenv("EMBEDDING_API_URL") or os.getenv("EMBEDDING_API_BASE") or os.getenv("LLM_API_BASE"),
        help="OpenAI-compatible embedding API base or full /embeddings URL.",
    )
    prepare.add_argument(
        "--embedding-api-key",
        default=os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY"),
        help="Embedding API key. Defaults to EMBEDDING_API_KEY or LLM_API_KEY.",
    )
    prepare.add_argument(
        "--embedding-model",
        default=os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-small"),
        help="Embedding model name sent to the provider.",
    )
    prepare.add_argument(
        "--embedding-dimension",
        type=int,
        default=int(os.getenv("EMBEDDING_DIMENSION", "64")),
        help="Vector dimension for the hash provider.",
    )
    prepare.add_argument(
        "--embedding-batch-size",
        type=int,
        default=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
        help="Number of schema documents embedded per request.",
    )
    prepare.add_argument(
        "--vector-store",
        default=os.getenv("SCHEMA_VECTOR_STORE", "faiss"),
        choices=["faiss", "jsonl"],
        help="Schema vector index backend. FAISS is the production target; JSONL is for offline tests.",
    )
    prepare.set_defaults(func=prepare_bird_command)

    args = parser.parse_args(argv)
    return args.func(args)


def prepare_bird_command(args: argparse.Namespace) -> int:
    paths = resolve_paths(args.raw_dir, args.db_dir, args.out_dir)
    ensure_output_dirs(paths, force=args.force)

    tables_path = paths.minidev_dir / "dev_tables.json"
    questions_path = paths.minidev_dir / "mini_dev_sqlite.json"
    if not tables_path.exists():
        raise SystemExit(f"Missing required file: {tables_path}")
    if not questions_path.exists():
        raise SystemExit(f"Missing required file: {questions_path}")

    tables = read_json(tables_path)
    questions = read_json(questions_path)
    if not isinstance(tables, list) or not isinstance(questions, list):
        raise SystemExit("BIRD Mini-Dev files must be JSON arrays.")

    table_by_db = {item["db_id"]: item for item in tables}
    questions_by_db: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_question in questions:
        questions_by_db[raw_question["db_id"]].append(raw_question)

    copied_db_paths = copy_sqlite_databases(paths)
    schemas = build_and_write_schemas(paths, table_by_db, copied_db_paths, questions_by_db)
    normalized_questions = normalize_questions(questions, schemas)
    selected_questions = select_demo_questions(
        normalized_questions,
        demo_db_limit=args.demo_db_limit,
        demo_question_limit=args.demo_question_limit,
    )

    validation_by_id: dict[str, dict[str, Any]] = {}
    if args.validate_sql or args.build_cache:
        validation_by_id = validate_questions(
            selected_questions,
            copied_db_paths,
            paths.out_dir / "execution_cache.jsonl",
            build_cache=args.build_cache,
            max_rows=args.max_rows,
        )

    databases = build_databases_index(schemas, copied_db_paths, normalized_questions, validation_by_id, paths)
    write_json(paths.out_dir / "databases.json", databases)
    write_jsonl(paths.out_dir / "questions.jsonl", selected_questions)
    write_jsonl(paths.out_dir / "gold_sql.jsonl", build_gold_sql_rows(selected_questions))
    write_demo_manifest(paths, selected_questions, databases, args)

    vector_manifest = None
    if args.build_embeddings:
        vector_manifest = build_schema_embeddings(args, schemas, paths)

    write_report(paths, normalized_questions, selected_questions, validation_by_id, args, vector_manifest)

    summary = {
        "status": "ok",
        "raw_dir": str(paths.raw_dir),
        "out_dir": str(paths.out_dir),
        "databases": len(databases),
        "questions": len(selected_questions),
        "validated": bool(validation_by_id),
        "execution_success_rate": compute_success_rate(validation_by_id),
    }
    if vector_manifest:
        summary["vector_index"] = {
            "index_type": vector_manifest["index_type"],
            "document_count": vector_manifest["document_count"],
            "dimension": vector_manifest["dimension"],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_schema_embeddings(args: argparse.Namespace, schemas: dict[str, dict[str, Any]], paths: PreparedPaths) -> dict[str, Any]:
    try:
        from askdata.embeddings import BuildEmbeddingClient
        from askdata.vector_store import BuildSchemaVectorIndex
    except ModuleNotFoundError:
        from embeddings import BuildEmbeddingClient
        from vector_store import BuildSchemaVectorIndex

    client = BuildEmbeddingClient(
        provider=args.embedding_provider,
        model=args.embedding_model,
        api_base=args.embedding_api_base,
        api_key=args.embedding_api_key,
        dimension=args.embedding_dimension,
    )
    return BuildSchemaVectorIndex(
        schemas=schemas,
        out_dir=paths.out_dir,
        embedding_client=client,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        vector_store=args.vector_store,
        batch_size=args.embedding_batch_size,
    )


def resolve_paths(raw_dir: Path, db_dir: Path, out_dir: Path) -> PreparedPaths:
    raw_dir = raw_dir.resolve()
    minidev_dir = raw_dir / "MINIDEV" if (raw_dir / "MINIDEV").is_dir() else raw_dir
    return PreparedPaths(
        raw_dir=raw_dir,
        minidev_dir=minidev_dir,
        db_dir=db_dir.resolve(),
        out_dir=out_dir.resolve(),
        schemas_dir=(out_dir / "schemas").resolve(),
        schema_prompts_dir=(out_dir / "schema_prompts").resolve(),
        demo_dir=(out_dir / "demo").resolve(),
    )


def ensure_output_dirs(paths: PreparedPaths, force: bool) -> None:
    if paths.out_dir.exists() and force:
        shutil.rmtree(paths.out_dir)
    paths.db_dir.mkdir(parents=True, exist_ok=True)
    paths.out_dir.mkdir(parents=True, exist_ok=True)
    paths.schemas_dir.mkdir(parents=True, exist_ok=True)
    paths.schema_prompts_dir.mkdir(parents=True, exist_ok=True)
    paths.demo_dir.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def copy_sqlite_databases(paths: PreparedPaths) -> dict[str, Path]:
    source_root = paths.minidev_dir / "dev_databases"
    if not source_root.exists():
        raise SystemExit(f"Missing BIRD database directory: {source_root}")

    copied: dict[str, Path] = {}
    for source_db in sorted(source_root.glob("*/*.sqlite")):
        if is_artifact_path(source_db):
            continue
        database_id = source_db.stem
        target_dir = paths.db_dir / database_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_db = target_dir / source_db.name
        if not target_db.exists() or source_db.stat().st_size != target_db.stat().st_size:
            shutil.copy2(source_db, target_db)
        copied[database_id] = target_db
    if not copied:
        raise SystemExit(f"No SQLite databases found under {source_root}")
    return copied


def is_artifact_path(path: Path) -> bool:
    return any(
        part == "__MACOSX"
        or part == ".DS_Store"
        or part.startswith("._")
        for part in path.parts
    )


def build_and_write_schemas(
    paths: PreparedPaths,
    table_by_db: dict[str, dict[str, Any]],
    copied_db_paths: dict[str, Path],
    questions_by_db: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for database_id, db_path in sorted(copied_db_paths.items()):
        metadata = table_by_db.get(database_id)
        if metadata is None:
            raise SystemExit(f"Missing table metadata for database_id={database_id}")
        schema = build_schema(database_id, db_path, metadata, len(questions_by_db.get(database_id, [])))
        schemas[database_id] = schema
        write_json(paths.schemas_dir / f"{database_id}.json", schema)
        (paths.schema_prompts_dir / f"{database_id}.md").write_text(
            build_schema_prompt(schema),
            encoding="utf-8",
        )
    return schemas


def build_schema(database_id: str, db_path: Path, metadata: dict[str, Any], question_count: int) -> dict[str, Any]:
    table_names = metadata["table_names_original"]
    readable_table_names = metadata.get("table_names", table_names)
    primary_key_indexes = flatten_primary_keys(metadata.get("primary_keys", []))
    foreign_key_indexes = metadata.get("foreign_keys", [])
    columns_by_table = build_columns_by_table(metadata, primary_key_indexes)

    sqlite_info = inspect_sqlite(db_path)
    tables: list[dict[str, Any]] = []
    for table_index, table_name in enumerate(table_names):
        tables.append(
            {
                "table_name": table_name,
                "display_name": readable_table_names[table_index],
                "row_count": sqlite_info.get(table_name, {}).get("row_count"),
                "columns": columns_by_table.get(table_index, []),
            }
        )

    foreign_keys = []
    column_refs = metadata["column_names_original"]
    for source_index, target_index in foreign_key_indexes:
        source_table_id, source_column = column_refs[source_index]
        target_table_id, target_column = column_refs[target_index]
        if source_table_id >= 0 and target_table_id >= 0:
            foreign_keys.append(
                {
                    "source_table": table_names[source_table_id],
                    "source_column": source_column,
                    "target_table": table_names[target_table_id],
                    "target_column": target_column,
                }
            )

    return {
        "database_id": database_id,
        "database_path": relpath(db_path),
        "table_count": len(tables),
        "column_count": sum(len(table["columns"]) for table in tables),
        "question_count": question_count,
        "tables": tables,
        "foreign_keys": foreign_keys,
    }


def flatten_primary_keys(primary_keys: list[Any]) -> set[int]:
    flattened: set[int] = set()
    for item in primary_keys:
        if isinstance(item, list):
            flattened.update(int(value) for value in item)
        else:
            flattened.add(int(item))
    return flattened


def build_columns_by_table(metadata: dict[str, Any], primary_key_indexes: set[int]) -> dict[int, list[dict[str, Any]]]:
    columns_by_table: dict[int, list[dict[str, Any]]] = defaultdict(list)
    original_columns = metadata["column_names_original"]
    readable_columns = metadata.get("column_names", original_columns)
    column_types = metadata.get("column_types", [])
    for column_index, (table_index, column_name) in enumerate(original_columns):
        if table_index < 0:
            continue
        readable_name = readable_columns[column_index][1] if column_index < len(readable_columns) else column_name
        data_type = column_types[column_index] if column_index < len(column_types) else "unknown"
        columns_by_table[table_index].append(
            {
                "column_name": column_name,
                "display_name": readable_name,
                "data_type": data_type,
                "is_primary_key": column_index in primary_key_indexes,
                "description": readable_name if readable_name != column_name else "",
            }
        )
    return columns_by_table


def inspect_sqlite(db_path: Path) -> dict[str, dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as conn:
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table_name,) in table_rows:
            row_count = None
            try:
                row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            except sqlite3.Error:
                pass
            info[table_name] = {"row_count": row_count}
    return info


def build_schema_prompt(schema: dict[str, Any]) -> str:
    lines = [
        f"# Database: {schema['database_id']}",
        "",
        "Use only the tables and columns listed below when generating SQL.",
        "",
        "## Tables",
    ]
    for table in schema["tables"]:
        lines.append(f"### {table['table_name']}")
        if table.get("display_name") and table["display_name"] != table["table_name"]:
            lines.append(f"- Business name: {table['display_name']}")
        if table.get("row_count") is not None:
            lines.append(f"- Row count: {table['row_count']}")
        lines.append("- Columns:")
        for column in table["columns"]:
            pk = " primary key" if column["is_primary_key"] else ""
            display = f", business name: {column['display_name']}" if column.get("display_name") else ""
            lines.append(f"  - {column['column_name']} ({column['data_type']}{pk}{display})")
        lines.append("")
    if schema["foreign_keys"]:
        lines.append("## Foreign Keys")
        for fk in schema["foreign_keys"]:
            lines.append(
                f"- {fk['source_table']}.{fk['source_column']} -> "
                f"{fk['target_table']}.{fk['target_column']}"
            )
        lines.append("")
    return "\n".join(lines)


def normalize_questions(questions: list[dict[str, Any]], schemas: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in questions:
        database_id = raw["db_id"]
        gold_sql = raw["SQL"].strip().rstrip(";")
        schema = schemas.get(database_id, {})
        tables, columns = infer_sql_references(gold_sql, schema)
        normalized.append(
            {
                "question_id": f"bird_{int(raw['question_id']):04d}",
                "source_question_id": raw["question_id"],
                "database_id": database_id,
                "question": raw["question"],
                "evidence": raw.get("evidence", ""),
                "gold_sql": gold_sql,
                "query_type": classify_query(gold_sql),
                "query_features": classify_query_features(gold_sql),
                "difficulty": raw.get("difficulty", "unknown"),
                "tables": tables,
                "columns": columns,
            }
        )
    normalized.sort(key=lambda item: (item["database_id"], item["source_question_id"]))
    return normalized


def infer_sql_references(gold_sql: str, schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    lower_sql = gold_sql.lower()
    tables: list[str] = []
    columns: list[str] = []
    for table in schema.get("tables", []):
        table_name = table["table_name"]
        if token_in_sql(table_name, lower_sql):
            tables.append(table_name)
        for column in table["columns"]:
            column_name = column["column_name"]
            if token_in_sql(column_name, lower_sql):
                columns.append(column_name)
    return sorted(set(tables)), sorted(set(columns))


def token_in_sql(token: str, lower_sql: str) -> bool:
    return token.lower() in lower_sql


def classify_query(sql: str) -> str:
    features = classify_query_features(sql)
    for candidate in ["nested", "join", "group_by", "order_by_limit", "aggregation", "where", "select"]:
        if candidate in features:
            return candidate
    return "select"


def classify_query_features(sql: str) -> list[str]:
    sql_lower = sql.lower()
    features: list[str] = ["select"] if "select" in sql_lower else []
    if sql_lower.count("select") > 1 or any(
        marker in sql_lower for marker in [" from (select", " in (select", " exists (select"]
    ):
        features.append("nested")
    if " join " in sql_lower:
        features.append("join")
    if " where " in sql_lower:
        features.append("where")
    if " group by " in sql_lower:
        features.append("group_by")
    if " order by " in sql_lower or " limit " in sql_lower:
        features.append("order_by_limit")
    if any(func in sql_lower for func in ["count(", "sum(", "avg(", "min(", "max("]):
        features.append("aggregation")
    return features or ["select"]


def select_demo_questions(
    questions: list[dict[str, Any]],
    demo_db_limit: int,
    demo_question_limit: int,
) -> list[dict[str, Any]]:
    chosen_db_ids = sorted({item["database_id"] for item in questions})[:demo_db_limit]
    candidate_questions = [item for item in questions if item["database_id"] in chosen_db_ids]
    by_db: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidate_questions:
        by_db[item["database_id"]].append(item)
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidate_questions:
        by_type[item["query_type"]].append(item)
        for feature in item.get("query_features", []):
            by_type[feature].append(item)

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # First, guarantee database coverage so the demo is not concentrated in a few DBs.
    per_db_target = max(1, min(3, demo_question_limit // max(1, len(chosen_db_ids))))
    for database_id in chosen_db_ids:
        for item in by_db.get(database_id, [])[:per_db_target]:
            if len(selected) >= demo_question_limit:
                break
            selected.append(item)
            seen_ids.add(item["question_id"])

    # Then, round-robin by query feature/type to preserve SQL complexity coverage.
    preferred_types = ["select", "where", "aggregation", "group_by", "order_by_limit", "join", "nested"]
    while len(selected) < demo_question_limit:
        added = False
        for query_type in preferred_types:
            bucket = by_type.get(query_type, [])
            while bucket and bucket[0]["question_id"] in seen_ids:
                bucket.pop(0)
            if bucket and len(selected) < demo_question_limit:
                item = bucket.pop(0)
                selected.append(item)
                seen_ids.add(item["question_id"])
                added = True
        if not added:
            break

    if len(selected) < demo_question_limit:
        for item in candidate_questions:
            if item["question_id"] not in seen_ids:
                selected.append(item)
                seen_ids.add(item["question_id"])
                if len(selected) >= demo_question_limit:
                    break
    selected.sort(key=lambda item: item["question_id"])
    return selected


def validate_questions(
    questions: list[dict[str, Any]],
    copied_db_paths: dict[str, Path],
    cache_path: Path,
    build_cache: bool,
    max_rows: int,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    cache_rows: list[dict[str, Any]] = []
    for question in questions:
        db_path = copied_db_paths[question["database_id"]]
        start = perf_counter()
        result: dict[str, Any] = {
            "question_id": question["question_id"],
            "database_id": question["database_id"],
            "gold_sql": question["gold_sql"],
            "success": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": None,
            "latency_ms": None,
        }
        try:
            columns, rows = execute_sql(db_path, question["gold_sql"], max_rows=max_rows)
            result.update(
                {
                    "success": True,
                    "columns": columns,
                    "rows": rows if build_cache else [],
                    "row_count": len(rows),
                    "latency_ms": round((perf_counter() - start) * 1000, 2),
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported in preprocessing output.
            result.update(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": round((perf_counter() - start) * 1000, 2),
                }
            )
        results[question["question_id"]] = result
        cache_rows.append(result)
    write_jsonl(cache_path, cache_rows)
    return results


def execute_sql(db_path: Path, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description or []]
        rows = [list(row) for row in cursor.fetchmany(max_rows)]
    return columns, rows


def build_databases_index(
    schemas: dict[str, dict[str, Any]],
    copied_db_paths: dict[str, Path],
    questions: list[dict[str, Any]],
    validation_by_id: dict[str, dict[str, Any]],
    paths: PreparedPaths,
) -> list[dict[str, Any]]:
    question_count = Counter(item["database_id"] for item in questions)
    success_count: Counter[str] = Counter()
    for result in validation_by_id.values():
        if result["success"]:
            success_count[result["database_id"]] += 1
    rows = []
    for database_id, schema in sorted(schemas.items()):
        rows.append(
            {
                "database_id": database_id,
                "database_path": relpath(copied_db_paths[database_id]),
                "schema_path": relpath(paths.schemas_dir / f"{database_id}.json"),
                "schema_prompt_path": relpath(paths.schema_prompts_dir / f"{database_id}.md"),
                "table_count": schema["table_count"],
                "column_count": schema["column_count"],
                "question_count": question_count.get(database_id, 0),
                "executable_question_count": success_count.get(database_id, 0) if validation_by_id else None,
            }
        )
    return rows


def build_gold_sql_rows(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": item["question_id"],
            "database_id": item["database_id"],
            "gold_sql": item["gold_sql"],
        }
        for item in questions
    ]


def write_demo_manifest(
    paths: PreparedPaths,
    questions: list[dict[str, Any]],
    databases: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    selected_database_ids = sorted({item["database_id"] for item in questions})
    manifest = {
        "name": "bird_minidev_demo",
        "source": "BIRD Mini-Dev SQLite",
        "split": args.split,
        "raw_dir": relpath(paths.raw_dir),
        "out_dir": relpath(paths.out_dir),
        "database_count": len(selected_database_ids),
        "question_count": len(questions),
        "demo_db_limit": args.demo_db_limit,
        "demo_question_limit": args.demo_question_limit,
        "databases": [item for item in databases if item["database_id"] in selected_database_ids],
        "question_ids": [item["question_id"] for item in questions],
        "query_type_distribution": dict(Counter(item["query_type"] for item in questions)),
        "query_feature_distribution": dict(
            Counter(feature for item in questions for feature in item.get("query_features", []))
        ),
        "difficulty_distribution": dict(Counter(item["difficulty"] for item in questions)),
    }
    write_json(paths.demo_dir / "demo_manifest.json", manifest)


def write_report(
    paths: PreparedPaths,
    all_questions: list[dict[str, Any]],
    selected_questions: list[dict[str, Any]],
    validation_by_id: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    vector_manifest: dict[str, Any] | None = None,
) -> None:
    failures = [
        {
            "question_id": result["question_id"],
            "database_id": result["database_id"],
            "error": result["error"],
            "gold_sql": result["gold_sql"],
        }
        for result in validation_by_id.values()
        if not result["success"]
    ]
    report = {
        "status": "completed",
        "source": "BIRD Mini-Dev SQLite",
        "raw_question_count": len(all_questions),
        "selected_question_count": len(selected_questions),
        "selected_database_count": len({item["database_id"] for item in selected_questions}),
        "query_type_distribution": dict(Counter(item["query_type"] for item in selected_questions)),
        "query_feature_distribution": dict(
            Counter(feature for item in selected_questions for feature in item.get("query_features", []))
        ),
        "difficulty_distribution": dict(Counter(item["difficulty"] for item in selected_questions)),
        "validation_enabled": bool(validation_by_id),
        "validated_count": len(validation_by_id),
        "validation_success_count": sum(1 for item in validation_by_id.values() if item["success"]),
        "validation_failure_count": len(failures),
        "execution_success_rate": compute_success_rate(validation_by_id),
        "failure_samples": failures[:20],
        "outputs": {
            "databases": relpath(paths.out_dir / "databases.json"),
            "schemas": relpath(paths.schemas_dir),
            "questions": relpath(paths.out_dir / "questions.jsonl"),
            "gold_sql": relpath(paths.out_dir / "gold_sql.jsonl"),
            "schema_prompts": relpath(paths.schema_prompts_dir),
            "demo_manifest": relpath(paths.demo_dir / "demo_manifest.json"),
            "execution_cache": relpath(paths.out_dir / "execution_cache.jsonl"),
        },
        "arguments": {
            "raw_dir": str(args.raw_dir),
            "db_dir": str(args.db_dir),
            "out_dir": str(args.out_dir),
            "demo_db_limit": args.demo_db_limit,
            "demo_question_limit": args.demo_question_limit,
            "split": args.split,
            "validate_sql": args.validate_sql,
            "build_cache": args.build_cache,
            "max_rows": args.max_rows,
            "build_embeddings": args.build_embeddings,
            "embedding_provider": args.embedding_provider,
            "embedding_model": args.embedding_model,
            "embedding_batch_size": args.embedding_batch_size,
            "vector_store": args.vector_store,
        },
    }
    if vector_manifest:
        report["outputs"]["vector_index"] = relpath(paths.out_dir / "vector_index")
        report["vector_index"] = vector_manifest
    write_json(paths.out_dir / "preprocess_report.json", report)


def compute_success_rate(validation_by_id: dict[str, dict[str, Any]]) -> float | None:
    if not validation_by_id:
        return None
    return round(
        sum(1 for item in validation_by_id.values() if item["success"]) / len(validation_by_id),
        4,
    )


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
