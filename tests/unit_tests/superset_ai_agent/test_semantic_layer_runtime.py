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

from __future__ import annotations

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.runtime import merge_indexed_semantic_context
from superset_ai_agent.semantic_layer.schemas import SemanticLayerVersion
from superset_ai_agent.semantic_layer.store import scope_hash


def test_merge_indexed_semantic_context_combines_runtime_and_indexed_items() -> None:
    scope = ConversationScope(database_id=1, schema_name="sales")
    store = InMemorySemanticLayerStore()
    store.save_version(
        SemanticLayerVersion(
            scope=scope,
            scope_hash=scope_hash(scope),
            version="v1",
            status="idle",
            wren_context=WrenContextArtifact(
                enabled=True,
                available=True,
                document_ids=["doc-2"],
                semantic_layer_version="v1",
                indexing_status="indexed",
                context_items=[{"kind": "indexed"}],
                warnings=["indexed warning"],
            ),
        ),
        owner_id="analyst",
    )

    merged = merge_indexed_semantic_context(
        semantic_layer_store=store,
        scope=scope,
        owner_id="analyst",
        wren_context=WrenContextArtifact(
            enabled=True,
            available=True,
            document_ids=["doc-1"],
            context_items=[{"kind": "runtime"}],
            warnings=["runtime warning"],
        ),
    )

    assert merged.document_ids == ["doc-1", "doc-2"]
    assert merged.semantic_layer_version == "v1"
    assert merged.indexing_status == "indexed"
    assert merged.context_items == [{"kind": "runtime"}, {"kind": "indexed"}]
    assert merged.warnings == ["runtime warning", "indexed warning"]


def test_merge_indexed_semantic_context_noops_without_matching_version() -> None:
    scope = ConversationScope(database_id=1, schema_name="sales")
    context = WrenContextArtifact(enabled=True, available=True)

    assert (
        merge_indexed_semantic_context(
            semantic_layer_store=InMemorySemanticLayerStore(),
            scope=scope,
            owner_id="analyst",
            wren_context=context,
        )
        == context
    )
