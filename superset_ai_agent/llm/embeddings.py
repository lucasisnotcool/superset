# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Embedder seam — pluggable text embeddings for retrieval/memory.

`ModelClient` only does chat; embedding retrieval needs a separate seam. The
default `NullEmbedder` is unavailable, so the retriever factory degrades to
keyword search when no embedder is configured (governance: degrade closed).
"""

from __future__ import annotations

from typing import Protocol

from superset_ai_agent.config import AgentConfig


class Embedder(Protocol):
    """Provider-neutral text embedder."""

    def is_available(self) -> bool:
        """Return whether the embedder can produce vectors."""

    def dimensions(self) -> int:
        """Return the embedding vector dimensionality."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""


class NullEmbedder:
    """Unavailable embedder; signals the retriever to fall back to keyword."""

    def is_available(self) -> bool:
        return False

    def dimensions(self) -> int:
        return 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("NullEmbedder cannot embed; configure an embedder.")


class OpenAiEmbedder:
    """OpenAI-compatible embedder using the existing ``openai`` dependency."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        batch_size: int = 128,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._client: object | None = None

    def _ensure_client(self) -> object:
        if self._client is None:
            from openai import OpenAI  # lazy import

            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key and self._model)

    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = client.embeddings.create(  # type: ignore[attr-defined]
                model=self._model,
                input=batch,
            )
            vectors.extend(item.embedding for item in response.data)
        return vectors


def create_embedder(config: AgentConfig) -> Embedder:
    """Build the configured embedder; ``NullEmbedder`` when unconfigured.

    OpenAI credentials fall back to the shared ``OPENAI_*`` config so OpenAI
    users only set the embedder provider.
    """

    if config.embedder_provider != "openai":
        return NullEmbedder()
    api_key = config.embedder_api_key or config.openai_api_key
    if not api_key:
        return NullEmbedder()
    return OpenAiEmbedder(
        api_key=api_key,
        base_url=config.embedder_base_url or config.openai_base_url,
        model=config.embedder_model,
        dimensions=config.embedder_dimensions,
        batch_size=config.embedder_batch_size,
    )
