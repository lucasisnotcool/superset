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

"""Project-scoped golden queries (F3): schema, validation, manifest exclusion."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.golden_queries import (
    build_model_table_index,
    dump_golden_queries,
    golden_query_refs,
    GoldenQueriesFile,
    GoldenQuery,
    is_golden_queries_path,
    parse_golden_queries,
    upsert_golden_query,
    validate_golden_queries,
)
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    validate_mdl_file,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
)

_MODEL_MANIFEST = {
    "models": [
        {
            "name": "customers",
            "tableReference": {"schema": "crm", "table": "customers"},
        }
    ]
}


def _queries_json(*entries: dict) -> str:
    return json.dumps({"queries": list(entries)})


_ENTRY = {
    "name": "top customers",
    "question": "who are the top customers?",
    "semantic_sql": "SELECT * FROM customers",
}


def test_is_golden_queries_path_matches_reserved_name() -> None:
    assert is_golden_queries_path("queries.json")
    assert is_golden_queries_path("/queries.json")
    assert not is_golden_queries_path("models/customers.json")
    assert not is_golden_queries_path(None)


def test_golden_query_file_round_trips() -> None:
    file = GoldenQueriesFile(queries=[GoldenQuery(**_ENTRY)])
    restored = parse_golden_queries(dump_golden_queries(file))
    assert restored.queries[0].name == "top customers"
    assert restored.queries[0].verified_at is None


def test_validate_golden_queries_accepts_well_formed_and_rejects_missing() -> None:
    assert validate_golden_queries(_queries_json(_ENTRY)).valid
    missing = validate_golden_queries(_queries_json({"name": "x"}))
    assert not missing.valid
    assert any(m.code == "missing_question" for m in missing.messages)
    assert not validate_golden_queries("[]").valid  # not an object with queries
    assert not validate_golden_queries("nonsense").valid  # parse error


def test_upsert_is_idempotent_on_normalized_question() -> None:
    content = _queries_json(_ENTRY)
    updated = upsert_golden_query(
        content,
        GoldenQuery(
            name="top customers v2",
            question="  Who Are The TOP Customers? ",
            semantic_sql="SELECT 2",
        ),
    )
    parsed = parse_golden_queries(updated)
    assert len(parsed.queries) == 1
    assert parsed.queries[0].semantic_sql == "SELECT 2"  # refreshed in place


def test_golden_query_refs_resolve_models_to_physical_tables() -> None:
    index = build_model_table_index(_MODEL_MANIFEST)
    tables, schemas = golden_query_refs(
        "SELECT * FROM customers", model_table_index=index
    )
    assert tables == ["crm.customers"]
    assert schemas == ["crm"]
    # A model not present in the manifest contributes nothing.
    tables2, _ = golden_query_refs("SELECT * FROM unknown", model_table_index=index)
    assert tables2 == []


# --- 2A: kind-aware validation through the MDL file store ---------------------


def test_queries_file_validates_as_golden_not_as_mdl() -> None:
    # validate_mdl would reject this (no models -> empty_root); the kind-aware
    # dispatch routes it to the golden validator instead.
    assert validate_mdl_file("queries.json", _queries_json(_ENTRY)).valid


def test_queries_file_creates_and_activates_as_draft() -> None:
    store = InMemoryMdlFileStore()
    created = store.create(
        "proj",
        MdlFileCreateRequest(path="queries.json", content=_queries_json(_ENTRY)),
    )
    assert created.status == "draft"
    activated = store.update(created.id, MdlFileUpdateRequest(status="active"))
    assert activated.status == "active"


def test_queries_file_excluded_from_compiled_manifest() -> None:
    mf = MdlFile(
        project_id="proj",
        path="queries.json",
        filename="queries.json",
        content=_queries_json(_ENTRY),
        checksum="x",
    )
    manifest = compile_manifest([mf])
    # The golden-query 'queries' key never reaches wren-core's manifest.
    assert manifest.models == []
    assert manifest.views == []


# --- 2C: golden-query recall + merge -----------------------------------------

from superset_ai_agent.semantic_layer.golden_queries import (  # noqa: E402
    merge_recalled_examples,
    recall_golden_queries,
)
from superset_ai_agent.semantic_layer.memory_store import (  # noqa: E402
    NlSqlPair,
    RecallAccess,
)

_VERIFIED_ENTRY = {**_ENTRY, "verified_at": 123}


def _seed_project(store: InMemoryMdlFileStore, *, queries: str) -> None:
    model = json.dumps(
        {
            "models": [
                {
                    "name": "customers",
                    "tableReference": {"schema": "crm", "table": "customers"},
                    "columns": [{"name": "id", "type": "INTEGER"}],
                }
            ]
        }
    )
    mf = store.create(
        "proj",
        MdlFileCreateRequest(path="models/customers.json", content=model),
    )
    store.update(mf.id, MdlFileUpdateRequest(status="active"))
    qf = store.create(
        "proj", MdlFileCreateRequest(path="queries.json", content=queries)
    )
    store.update(qf.id, MdlFileUpdateRequest(status="active"))


def _crm_access() -> RecallAccess:
    return RecallAccess(
        accessible_tables=frozenset({"crm.customers"}),
        project_schemas=frozenset({"crm"}),
        onboarded_tables=frozenset({"crm.customers"}),
    )


def test_recall_golden_queries_surfaces_verified_pair() -> None:
    store = InMemoryMdlFileStore()
    _seed_project(store, queries=_queries_json(_VERIFIED_ENTRY))
    golden = recall_golden_queries(
        mdl_file_store=store,
        project_id="proj",
        owner_id="local",
        question="who are the top customers",
        k=3,
        access=_crm_access(),
    )
    assert len(golden) == 1
    assert golden[0].result_meta["golden"] is True
    assert golden[0].result_meta["verified"] is True


def test_recall_golden_queries_drops_inaccessible() -> None:
    store = InMemoryMdlFileStore()
    _seed_project(store, queries=_queries_json(_ENTRY))
    golden = recall_golden_queries(
        mdl_file_store=store,
        project_id="proj",
        owner_id="local",
        question="x",
        k=3,
        access=RecallAccess(accessible_tables=frozenset({"sales.deals"})),
    )
    assert golden == []


def test_recall_golden_queries_empty_without_project_or_file() -> None:
    assert recall_golden_queries(
        mdl_file_store=InMemoryMdlFileStore(),
        project_id=None,
        owner_id="local",
        question="x",
        k=3,
    ) == []
    # Project with no golden file -> empty (runtime memory stands alone).
    store = InMemoryMdlFileStore()
    assert recall_golden_queries(
        mdl_file_store=store, project_id="proj", owner_id="local", question="x", k=3
    ) == []


def test_merge_prioritizes_golden_and_supersedes_runtime_twin() -> None:
    golden = [NlSqlPair(question="top customers", semantic_sql="g", native_sql="g")]
    memory = [
        NlSqlPair(question="Top Customers", semantic_sql="m", native_sql="m"),  # twin
        NlSqlPair(question="other", semantic_sql="o", native_sql="o"),
    ]
    merged = merge_recalled_examples(golden, memory, 3)
    # Golden leads; its normalized twin from memory is suppressed (not duplicated).
    assert [p.question for p in merged] == ["top customers", "other"]
