"""Strict OpenAI-compatible embedding client used by optional retrieval."""

from __future__ import annotations

import math
from typing import Any, Sequence


class EmbeddingConfigurationError(ValueError):
    """The embedding service response does not match the configured contract."""


class EmbeddingClient:
    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "BAAI/bge-m3",
        dimension: int = 1024,
        timeout: float = 2.0,
        *,
        client: Any = None,
        api: Any = None,
    ) -> None:
        if dimension <= 0:
            raise EmbeddingConfigurationError("Embedding dimension must be positive")
        if api is None:
            if client is None:
                from openai import OpenAI

                client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
            api = client.embeddings
        self.api = api
        self.model = model
        self.dimension = dimension

    def Embed(self, texts: Sequence[str], batch_size: int = 32, max_chars: int = 1000) -> list[list[float]]:
        inputs = [t[:max_chars] for t in texts]
        if not inputs:
            return []
        # Respect server-side batch limits by sending in chunks.
        if len(inputs) <= batch_size:
            return self._Create(inputs)
        all_vectors: list[list[float]] = []
        for i in range(0, len(inputs), batch_size):
            all_vectors.extend(self._Create(inputs[i : i + batch_size]))
        return all_vectors

    def _Create(self, inputs: list[str]) -> list[list[float]]:
        response = self.api.create(model=self.model, input=inputs)
        returned_model = getattr(response, "model", None)
        if not isinstance(returned_model, str) or not returned_model.strip():
            raise EmbeddingConfigurationError(
                "Embedding response must declare model provenance"
            )
        if returned_model != self.model and not returned_model.startswith("/"):
            raise EmbeddingConfigurationError(
                f"Embedding model mismatch: expected {self.model}, got {returned_model}"
            )
        items = list(getattr(response, "data", []))
        if len(items) != len(inputs):
            raise EmbeddingConfigurationError(
                f"Embedding service returned {len(items)} vectors for {len(inputs)} texts"
            )

        if not all(hasattr(item, "index") for item in items):
            raise EmbeddingConfigurationError(
                "Every embedding item must provide an explicit index"
            )
        indices = [item.index for item in items]
        if (
            not all(isinstance(index, int) and not isinstance(index, bool) for index in indices)
            or sorted(indices) != list(range(len(inputs)))
            or len(set(indices)) != len(indices)
        ):
            raise EmbeddingConfigurationError("Embedding response indices are incomplete or duplicated")
        ordered = sorted(zip(indices, items), key=lambda pair: pair[0])
        vectors: list[list[float]] = []
        for _, item in ordered:
            vector = list(getattr(item, "embedding", []))
            if len(vector) != self.dimension:
                raise EmbeddingConfigurationError(
                    f"Embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
                )
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
                raise EmbeddingConfigurationError("Embedding vector contains a non-finite value")
            vectors.append([float(value) for value in vector])
        return vectors

    def Validate(self) -> list[float]:
        """Validate service model, dimension, and response shape with one probe."""
        return self.Embed(["AskData schema retrieval validation"])[0]
