from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from askdata.schema_documents import BuildSchemaDocuments
except ModuleNotFoundError:  # Direct script execution fallback.
    from schema_documents import BuildSchemaDocuments


def BuildSchemaVectorIndex(
    schemas: dict[str, dict[str, Any]] | list[dict[str, Any]],
    out_dir: Path,
    embedding_client,
    embedding_provider: str,
    embedding_model: str,
    vector_store: str = "faiss",
    batch_size: int = 64,
) -> dict[str, Any]:
    documents = BuildSchemaDocuments(schemas)
    if not documents:
        raise ValueError("No schema documents were produced for embedding.")
    if batch_size <= 0:
        raise ValueError("Embedding batch size must be positive.")

    texts = [document["text"] for document in documents]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedding_client.EmbedTexts(texts[start:start + batch_size]))
    dimension = _ValidateVectors(vectors, expected_count=len(documents))

    index_dir = out_dir / "vector_index"
    if index_dir.exists():
        shutil.rmtree(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = index_dir / "schema_metadata.jsonl"
    _WriteJsonl(metadata_path, documents)

    index_file = None
    vectors_file = None
    if vector_store == "faiss":
        index_file = "schema.faiss"
        _WriteFaiss(index_dir / index_file, vectors, dimension)
    elif vector_store == "jsonl":
        vectors_file = "schema_vectors.jsonl"
        _WriteJsonl(
            index_dir / vectors_file,
            [{"id": document["id"], "vector": vector} for document, vector in zip(documents, vectors)],
        )
    else:
        raise ValueError(f"Unsupported vector store: {vector_store}")

    manifest = {
        "version": 1,
        "source": "BIRD Mini-Dev SQLite",
        "index_type": vector_store,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "dimension": dimension,
        "document_count": len(documents),
        "document_types": dict(Counter(document["doc_type"] for document in documents)),
        "metadata_file": "schema_metadata.jsonl",
        "index_file": index_file,
        "vectors_file": vectors_file,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _WriteJson(index_dir / "manifest.json", manifest)
    return manifest


def _ValidateVectors(vectors: list[list[float]], expected_count: int) -> int:
    if len(vectors) != expected_count:
        raise ValueError(f"Embedding count mismatch: expected {expected_count}, got {len(vectors)}")
    if not vectors:
        raise ValueError("Embedding response is empty.")
    dimension = len(vectors[0])
    if dimension <= 0:
        raise ValueError("Embedding vectors must have positive dimension.")
    for index, vector in enumerate(vectors):
        if len(vector) != dimension:
            raise ValueError(f"Embedding vector {index} has dimension {len(vector)}, expected {dimension}")
    return dimension


def _WriteFaiss(path: Path, vectors: list[list[float]], dimension: int) -> None:
    try:
        import faiss  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FAISS vector store requires optional dependencies: pip install faiss-cpu numpy. "
            "Use --vector-store jsonl for offline contract tests."
        ) from exc

    array = np.asarray(vectors, dtype="float32")
    if array.shape != (len(vectors), dimension):
        raise ValueError(f"Invalid vector array shape: {array.shape}")
    index = faiss.IndexFlatL2(dimension)
    index.add(array)
    faiss.write_index(index, str(path))


def _WriteJson(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _WriteJsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
