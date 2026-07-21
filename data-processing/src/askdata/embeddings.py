from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from typing import Protocol


class EmbeddingClient(Protocol):
    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingClient:
    """Small stdlib client for OpenAI-compatible embedding endpoints."""

    def __init__(self, api_base: str | None, api_key: str | None, model: str, timeout_seconds: int = 60):
        if not api_base:
            raise ValueError("Embedding API base is required for openai-compatible embeddings.")
        self.endpoint = _EmbeddingEndpoint(api_base)
        self.api_key = api_key or ""
        self.model = model
        self.timeout_seconds = timeout_seconds

    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Embedding request failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Embedding request failed: {exc.reason}") from exc

        rows = data.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Embedding response is missing a data array.")
        rows = sorted(rows, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in rows]
        if len(vectors) != len(texts) or not all(isinstance(vector, list) for vector in vectors):
            raise RuntimeError("Embedding response count does not match input count.")
        return [[float(value) for value in vector] for vector in vectors]


class HashEmbeddingClient:
    """Deterministic local embeddings for tests and offline contract validation."""

    def __init__(self, dimension: int = 64):
        if dimension <= 0:
            raise ValueError("Hash embedding dimension must be positive.")
        self.dimension = dimension

    def EmbedTexts(self, texts: list[str]) -> list[list[float]]:
        return [self._Embed(text) for text in texts]

    def _Embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for index, token in enumerate(text.lower().split()):
            digest = hashlib.sha256(f"{index}:{token}".encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = -1.0 if digest[4] % 2 else 1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def BuildEmbeddingClient(
    provider: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    dimension: int = 64,
) -> EmbeddingClient:
    if provider == "openai-compatible":
        return OpenAICompatibleEmbeddingClient(
            api_base=api_base or os.getenv("EMBEDDING_API_URL") or os.getenv("EMBEDDING_API_BASE") or os.getenv("LLM_API_BASE"),
            api_key=api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY"),
            model=model,
        )
    if provider == "hash":
        return HashEmbeddingClient(dimension=dimension)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _EmbeddingEndpoint(api_base: str) -> str:
    cleaned = api_base.rstrip("/")
    if cleaned.endswith("/embeddings"):
        return cleaned
    return f"{cleaned}/embeddings"
