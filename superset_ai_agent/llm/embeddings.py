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

import logging
from typing import Protocol

from superset_ai_agent.config import AgentConfig

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """Provider-neutral text embedder."""

    def is_available(self) -> bool:
        """Return whether the embedder can produce vectors."""

    def dimensions(self) -> int:
        """Return the embedding vector dimensionality."""

    def signature(self) -> str:
        """Stable identity (provider:model:dims) used to key a vector index.

        Changing the model or dimension changes the signature, which forces a
        reindex so a persisted index never mixes vectors from different models
        (wren_full.md R3 / R-RET4).
        """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""


class NullEmbedder:
    """Unavailable embedder; signals the retriever to fall back to keyword."""

    def is_available(self) -> bool:
        return False

    def dimensions(self) -> int:
        return 0

    def signature(self) -> str:
        return "null"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("NullEmbedder cannot embed; configure an embedder.")


#: Native/known dimensions for common OpenAI embedding models. `text-embedding-3-*`
#: support reduced dimensions via the API `dimensions` arg (≤ the listed max);
#: `ada-002` is fixed and rejects the arg.
_OPENAI_MODEL_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAiEmbedder:
    """OpenAI-compatible embedder using the existing ``openai`` dependency."""

    provider = "openai"

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

    def signature(self) -> str:
        return f"{self.provider}:{self._model}:{self._dimensions}"

    def _supports_dimensions_arg(self) -> bool:
        # Only the text-embedding-3-* family accepts a reduced `dimensions` arg;
        # ada-002 (and unknown/self-hosted models) reject it, so omit it there.
        return self._model.startswith("text-embedding-3")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        kwargs: dict[str, object] = {"model": self._model}
        if self._supports_dimensions_arg() and self._dimensions > 0:
            kwargs["dimensions"] = self._dimensions  # request reduced vectors
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = client.embeddings.create(  # type: ignore[attr-defined]
                input=batch,
                **kwargs,
            )
            vectors.extend(item.embedding for item in response.data)
        return vectors


class OllamaEmbedder(OpenAiEmbedder):
    """Ollama embedder via its OpenAI-compatible ``/v1/embeddings`` endpoint."""

    provider = "ollama"

    def _supports_dimensions_arg(self) -> bool:
        # Ollama models do not accept the OpenAI `dimensions` argument.
        return False


def create_embedder(config: AgentConfig) -> Embedder:
    """Build the configured embedder; ``NullEmbedder`` when unconfigured.

    OpenAI credentials fall back to the shared ``OPENAI_*`` config so OpenAI
    users only set the embedder provider.
    """

    provider = (config.embedder_provider or "").lower()
    if provider == "ollama":
        return OllamaEmbedder(
            api_key=config.embedder_api_key or "ollama",  # ollama ignores the key
            base_url=config.embedder_base_url or config.ollama_base_url,
            model=config.embedder_model,
            dimensions=config.embedder_dimensions,
            batch_size=config.embedder_batch_size,
        )
    if provider != "openai":
        return NullEmbedder()
    api_key = config.embedder_api_key or config.openai_api_key
    if not api_key:
        return NullEmbedder()
    _warn_on_dimension_mismatch(config.embedder_model, config.embedder_dimensions)
    return OpenAiEmbedder(
        api_key=api_key,
        base_url=config.embedder_base_url or config.openai_base_url,
        model=config.embedder_model,
        dimensions=config.embedder_dimensions,
        batch_size=config.embedder_batch_size,
    )


def _warn_on_dimension_mismatch(model: str, dimensions: int) -> None:
    """Soft startup check: configured dimensions vs. the model's known maximum."""

    native = _OPENAI_MODEL_DIMS.get(model)
    if native is None:
        return  # unknown/custom model — cannot validate, trust the operator
    if model == "text-embedding-ada-002" and dimensions != native:
        logger.warning(
            "embedder_dimensions=%s but %s is fixed at %s; the value is ignored.",
            dimensions,
            model,
            native,
        )
    elif dimensions > native:
        logger.warning(
            "embedder_dimensions=%s exceeds %s's maximum %s; embeddings may fail.",
            dimensions,
            model,
            native,
        )
