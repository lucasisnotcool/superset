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

from superset_ai_agent.semantic_layer.schema_snapshot import (
    InMemorySchemaSnapshotStore,
    SchemaSnapshot,
)


def test_snapshot_store_upserts_and_reads() -> None:
    store = InMemorySchemaSnapshotStore()
    assert store.get("project-1") is None

    store.upsert(
        SchemaSnapshot(
            project_id="project-1",
            schema_name="sales",
            tables={"deals": ["stage", "gross_moves"]},
        )
    )
    fetched = store.get("project-1")
    assert fetched is not None
    assert fetched.tables == {"deals": ["stage", "gross_moves"]}

    # Upsert replaces the prior snapshot for the same project.
    store.upsert(
        SchemaSnapshot(
            project_id="project-1",
            schema_name="sales",
            tables={"deals": ["stage"]},
        )
    )
    assert store.get("project-1").tables == {"deals": ["stage"]}
