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

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.integrations.wren.factory import create_wren_client
from superset_ai_agent.integrations.wren.llm_client import LlmWrenClient
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import ModelInfo
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlValidationMessage,
    MdlValidationResult,
    SemanticDocument,
    SemanticProject,
)


class FakeModelClient:
    """Deterministic ModelClient stub returning a queued JSON payload."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        self.calls.append(messages)
        return ModelResult(content=self.content)

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return []


def _config() -> AgentConfig:
    return AgentConfig(wren_adapter="llm")


def _project() -> SemanticProject:
    return SemanticProject(
        name="examples.sales",
        owner_id="local",
        database_uri_fingerprint="fp",
        schema_name="sales",
        catalog_name=None,
        database_label="examples",
    )


def _agent_context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="sqlite"),
        datasets=[
            DatasetMetadata(
                id=7,
                table_name="deals",
                schema_name="sales",
                database_id=1,
                description="Sales deals",
                columns=[
                    ColumnSummary(name="stage", type="VARCHAR"),
                    ColumnSummary(name="gross_moves", type="BIGINT"),
                ],
                metrics=[
                    MetricSummary(name="total_moves", expression="SUM(gross_moves)")
                ],
            )
        ],
    )


def _document() -> SemanticDocument:
    return SemanticDocument(
        filename="glossary.md",
        content_type="text/markdown",
        size_bytes=42,
        scope=ConversationScope(
            database_id=1, catalog_name=None, schema_name="sales", dataset_ids=[]
        ),
        checksum="abc",
        storage_uri="mem://glossary.md",
        extracted_text="Gross moves means total units shifted across pipeline stages.",
    )


def test_generate_base_model_parses_llm_proposals() -> None:
    payload = {
        "files": [
            {
                "path": "models/deals.json",
                "manifest": {
                    "models": [
                        {
                            "name": "deals",
                            "description": "Sales deals and pipeline stages",
                            "tableReference": {"schema": "sales", "table": "deals"},
                            "columns": [{"name": "stage", "type": "VARCHAR"}],
                        }
                    ]
                },
            }
        ],
        "warnings": [],
    }
    client = LlmWrenClient(_config(), FakeModelClient(json.dumps(payload)))

    proposals = client.generate_base_model(
        project=_project(), superset_context=_agent_context()
    )

    assert len(proposals) == 1
    assert proposals[0].proposed_path == "models/deals.json"
    assert proposals[0].validation.valid is True
    assert any("review" in warning.lower() for warning in proposals[0].warnings)
    # The model returned a typed object; we serialized native camelCase JSON.
    parsed = json.loads(proposals[0].proposed_content)
    assert parsed["models"][0]["tableReference"]["table"] == "deals"


def test_generate_base_model_rejects_typeless_column() -> None:
    # A column missing the required `type` fails schema validation, so the typed
    # parse fails and we fall back to the deterministic (native) proposal.
    payload = {
        "files": [
            {
                "path": "models/deals.json",
                "manifest": {
                    "models": [
                        {
                            "name": "deals",
                            "tableReference": {"schema": "sales", "table": "deals"},
                            "columns": [{"name": "stage"}],  # no type
                        }
                    ]
                },
            }
        ]
    }
    client = LlmWrenClient(_config(), FakeModelClient(json.dumps(payload)))

    proposals = client.generate_base_model(
        project=_project(), superset_context=_agent_context()
    )

    # Deterministic fallback kicked in; every column still carries a type.
    assert len(proposals) == 1
    parsed = json.loads(proposals[0].proposed_content)
    assert all("type" in col for col in parsed["models"][0]["columns"])


def test_generate_base_model_seeds_structure_even_with_useless_model() -> None:
    # W3: a model that returns no usable semantics must not cost us structure.
    # Every column still carries its real type and a valid tableReference because
    # the structure is seeded from the datasets, not authored by the model.
    empty_payload = json.dumps({"files": [], "warnings": []})
    client = LlmWrenClient(_config(), FakeModelClient(empty_payload))

    proposals = client.generate_base_model(
        project=_project(), superset_context=_agent_context()
    )

    assert len(proposals) == 1
    parsed = json.loads(proposals[0].proposed_content)
    model = parsed["models"][0]
    assert model["tableReference"]["table"] == "deals"
    columns = {col["name"]: col for col in model["columns"]}
    assert columns["stage"]["type"] == "VARCHAR"
    assert columns["gross_moves"]["type"] == "BIGINT"
    assert proposals[0].validation.valid is True


def test_generate_base_model_overlays_llm_descriptions_onto_seed() -> None:
    # The model's descriptions are overlaid onto the seeded structure.
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/deals.json",
                    "manifest": {
                        "models": [
                            {
                                "name": "deals",
                                "description": "Sales pipeline deals",
                                "tableReference": {"schema": "sales", "table": "deals"},
                                "columns": [
                                    {
                                        "name": "stage",
                                        "type": "VARCHAR",
                                        "description": "Pipeline stage label",
                                    }
                                ],
                            }
                        ]
                    },
                }
            ]
        }
    )
    client = LlmWrenClient(_config(), FakeModelClient(payload))

    proposals = client.generate_base_model(
        project=_project(), superset_context=_agent_context()
    )

    parsed = json.loads(proposals[0].proposed_content)
    model = parsed["models"][0]
    assert model["description"] == "Sales pipeline deals"
    stage = next(c for c in model["columns"] if c["name"] == "stage")
    assert stage["description"] == "Pipeline stage label"
    # Structure (type) preserved from the seed regardless of the overlay.
    assert stage["type"] == "VARCHAR"


def test_generate_base_model_falls_back_on_bad_output() -> None:
    client = LlmWrenClient(_config(), FakeModelClient("not json at all"))

    proposals = client.generate_base_model(
        project=_project(), superset_context=_agent_context()
    )

    # Deterministic fallback: one proposal per dataset, native camelCase MDL.
    assert len(proposals) == 1
    parsed = json.loads(proposals[0].proposed_content)
    assert parsed["models"][0]["tableReference"]["table"] == "deals"
    # F5: the degradation (structure-only draft, no enrichment) is surfaced, not
    # hidden — the provider likely didn't honor structured output.
    assert any("structured" in warning.lower() for warning in proposals[0].warnings)


def test_propose_mdl_from_document_surfaces_fallback_warning() -> None:
    # The model is invoked (there is document text) but returns unparseable
    # output; the deterministic draft is returned with a visible degradation note.
    client = LlmWrenClient(_config(), FakeModelClient("not json at all"))

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert any("structured" in warning.lower() for warning in proposal.warnings)


def test_propose_mdl_from_document_uses_llm() -> None:
    payload = {
        "files": [
            {
                "path": "models/deals.json",
                "manifest": {
                    "models": [
                        {
                            "name": "deals",
                            "description": "enriched",
                            "tableReference": {"schema": "sales", "table": "deals"},
                            "columns": [{"name": "stage", "type": "VARCHAR"}],
                        }
                    ]
                },
            }
        ]
    }
    client = LlmWrenClient(_config(), FakeModelClient(json.dumps(payload)))
    document = _document()

    proposal = client.propose_mdl_from_document(project=_project(), document=document)

    assert proposal.source_document_id == document.id
    assert proposal.validation.valid is True
    assert "enriched" in proposal.proposed_content


class _FakeFileStore:
    """Minimal MDL file store exposing only ``list`` (what targeting needs)."""

    def __init__(self, files: list[MdlFile]) -> None:
        self._files = files

    def list(self, project_id: str, *, owner_id: str = "local") -> list[MdlFile]:
        return list(self._files)


def _active_file(path: str, models: list[dict[str, Any]]) -> MdlFile:
    return MdlFile(
        project_id="project-1",
        path=path,
        filename=path.split("/")[-1],
        content=json.dumps({"models": models}),
        checksum="c",
        status="active",
    )


def _draft_file(path: str, models: list[dict[str, Any]]) -> MdlFile:
    return MdlFile(
        project_id="project-1",
        path=path,
        filename=path.split("/")[-1],
        content=json.dumps({"models": models}),
        checksum="c",
        status="draft",
    )


def _model(name: str, **extra: Any) -> dict[str, Any]:
    model = {
        "name": name,
        "tableReference": {"table": name},
        "columns": [{"name": "id", "type": "INT"}],
    }
    model.update(extra)
    return model


def _enrich_payload(models: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"files": [{"path": "models/whatever.json", "manifest": {"models": models}}]}
    )


def test_propose_mdl_patches_owning_file_among_many() -> None:
    # F6: two active files; enrichment touches a model that lives in the second.
    store = _FakeFileStore(
        [
            _active_file("models/a.json", [_model("alpha")]),
            _active_file("models/b.json", [_model("beta"), _model("gamma")]),
        ]
    )
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([_model("beta", description="enriched")])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    # Targets the owning file in place — not a colliding new sibling.
    assert proposal.proposed_path == "models/b.json"
    merged = json.loads(proposal.proposed_content)
    names = [model["name"] for model in merged["models"]]
    # gamma (untouched) is preserved; beta is enriched in place; alpha (other
    # file) is not dragged in.
    assert names == ["beta", "gamma"]
    assert "alpha" not in names
    beta = next(model for model in merged["models"] if model["name"] == "beta")
    assert beta["description"] == "enriched"


def test_propose_mdl_falls_back_when_overlay_spans_files() -> None:
    # F6: the overlay touches models in two different files — no single owner, so
    # it falls back to the model's path and lets the activation dedup net handle it.
    store = _FakeFileStore(
        [
            _active_file("models/a.json", [_model("alpha")]),
            _active_file("models/b.json", [_model("beta")]),
        ]
    )
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([_model("alpha"), _model("beta")])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.proposed_path == "models/whatever.json"


def test_propose_mdl_appends_new_model_to_single_active_file() -> None:
    # F6 generalizes W4: a brand-new model lands in the lone active file (merged,
    # preserving the existing one) instead of a colliding sibling.
    store = _FakeFileStore([_active_file("models/a.json", [_model("alpha")])])
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([_model("delta")])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.proposed_path == "models/a.json"
    names = [model["name"] for model in json.loads(proposal.proposed_content)["models"]]
    assert names == ["alpha", "delta"]


def test_fetch_context_surfaces_materialized_mdl(tmp_path) -> None:
    mdl_path = tmp_path / "mdl.json"
    mdl_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "name": "deals",
                        "description": "Sales deal stages and gross moves",
                        "columns": [
                            {"name": "gross_moves", "description": "units shifted"}
                        ],
                    }
                ],
                "relationships": [{"name": "deals_site", "join_type": "MANY_TO_ONE"}],
            }
        ),
        encoding="utf-8",
    )
    client = LlmWrenClient(_config(), FakeModelClient("{}"))

    context = client.fetch_context(
        question="show gross moves by stage",
        superset_context=_agent_context(),
        mdl_path=str(mdl_path),
    )

    assert context.available is True
    assert context.matched_models == ["deals"]
    model_items = [
        item for item in context.context_items if item.get("type") == "model"
    ]
    assert model_items
    assert model_items[0]["model"]["description"].startswith("Sales deal")
    assert any(item.get("type") == "relationships" for item in context.context_items)
    # fetch_context must be deterministic and never call the model.
    assert client.model_client.calls == []


def test_fetch_context_surfaces_matching_view_excludes_native(tmp_path) -> None:
    mdl_path = tmp_path / "mdl.json"
    mdl_path.write_text(
        json.dumps(
            {
                "models": [
                    {"name": "deals", "columns": [{"name": "amount"}]},
                ],
                "views": [
                    {
                        "name": "warm_line_output",
                        "statement": "SELECT amount FROM deals",
                        "properties": {"description": "warm line output by family"},
                    },
                    {
                        "name": "legacy_rollup",
                        "statement": "SELECT * FROM public.raw",
                        "dialect": "postgres",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    client = LlmWrenClient(_config(), FakeModelClient("{}"))

    context = client.fetch_context(
        question="warm line output by family",
        superset_context=_agent_context(),
        mdl_path=str(mdl_path),
    )

    # The matching semantic view is surfaced; the native view never is.
    assert context.matched_views == ["warm_line_output"]
    view_items = [i for i in context.context_items if i.get("type") == "views"]
    assert view_items
    surfaced = {v["name"] for v in view_items[0]["items"]}
    assert surfaced == {"warm_line_output"}


def test_fetch_context_omits_views_when_none_match(tmp_path) -> None:
    mdl_path = tmp_path / "mdl.json"
    mdl_path.write_text(
        json.dumps(
            {
                "models": [{"name": "deals", "columns": [{"name": "amount"}]}],
                "views": [
                    {
                        "name": "unrelated_view",
                        "statement": "SELECT 1 FROM deals",
                        "properties": {"description": "something else entirely"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = LlmWrenClient(_config(), FakeModelClient("{}"))

    context = client.fetch_context(
        question="gross moves by region",
        superset_context=_agent_context(),
        mdl_path=str(mdl_path),
    )
    # A view that shares no terms with the question never crowds the context.
    assert context.matched_views == []
    assert not any(i.get("type") == "views" for i in context.context_items)


def test_fetch_context_unavailable_without_mdl() -> None:
    client = LlmWrenClient(_config(), FakeModelClient("{}"))

    context = client.fetch_context(
        question="anything",
        superset_context=_agent_context(),
        mdl_path=None,
    )

    assert context.available is False
    assert context.warnings


def test_llm_client_has_no_execution_methods() -> None:
    client = LlmWrenClient(_config(), FakeModelClient("{}"))

    for forbidden in ("execute", "run_sql", "query", "query_preview"):
        assert not hasattr(client, forbidden)


def test_default_overlay_path_routes_view_only_overlay_to_views_dir() -> None:
    from superset_ai_agent.integrations.wren.llm_client import _default_overlay_path

    view_only = {"views": [{"name": "Big Orders", "statement": "SELECT 1"}]}
    assert _default_overlay_path(_project(), view_only) == "views/big_orders.json"

    # A mixed overlay (has models) keeps the model-file default.
    mixed = {"models": [{"name": "orders"}], "views": [{"name": "v"}]}
    assert _default_overlay_path(_project(), mixed) == "models/sales.json"

    # No views, no models → model-file default.
    assert _default_overlay_path(_project(), {}) == "models/sales.json"


def test_factory_returns_llm_client_with_model_client() -> None:
    client = create_wren_client(_config(), model_client=FakeModelClient("{}"))
    assert isinstance(client, LlmWrenClient)


def test_factory_falls_back_without_model_client() -> None:
    client = create_wren_client(_config(), model_client=None)
    assert not isinstance(client, LlmWrenClient)


# --- E4/E5: enrichment preserves column structure of touched models -----------


def _model_with_columns(
    name: str, columns: list[dict[str, Any]], **extra: Any
) -> dict[str, Any]:
    model: dict[str, Any] = {
        "name": name,
        "tableReference": {"table": name},
        "columns": columns,
    }
    model.update(extra)
    return model


def test_enrichment_preserves_omitted_column_on_touched_model() -> None:
    # E4: the overlay re-emits "beta" but omits the "amount" column. The merge
    # must keep "amount" (with its type) rather than dropping it.
    store = _FakeFileStore(
        [
            _active_file(
                "models/b.json",
                [
                    _model_with_columns(
                        "beta",
                        [
                            {"name": "id", "type": "INT"},
                            {"name": "amount", "type": "BIGINT"},
                        ],
                    )
                ],
            )
        ]
    )
    overlay_model = _model_with_columns(
        "beta",
        [{"name": "id", "type": "INT", "description": "identifier"}],
        description="enriched beta",
    )
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([overlay_model])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.proposed_path == "models/b.json"
    beta = next(
        model
        for model in json.loads(proposal.proposed_content)["models"]
        if model["name"] == "beta"
    )
    column_names = [column["name"] for column in beta["columns"]]
    assert column_names == ["id", "amount"]  # amount preserved
    amount = next(col for col in beta["columns"] if col["name"] == "amount")
    assert amount["type"] == "BIGINT"
    # Semantics from the overlay are still applied to the touched column/model.
    identifier = next(col for col in beta["columns"] if col["name"] == "id")
    assert identifier["description"] == "identifier"
    assert beta["description"] == "enriched beta"
    # No drop happened, so no drop warning is surfaced (no false positives).
    assert not any(
        "dropped existing column" in warning for warning in proposal.warnings
    )


def test_enrichment_does_not_retype_existing_column() -> None:
    # E4: the overlay tries to change "id" from INT to VARCHAR. Physical type is
    # authoritative and must survive.
    store = _FakeFileStore(
        [
            _active_file(
                "models/b.json",
                [_model_with_columns("beta", [{"name": "id", "type": "INT"}])],
            )
        ]
    )
    overlay_model = _model_with_columns(
        "beta", [{"name": "id", "type": "VARCHAR", "description": "id col"}]
    )
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([overlay_model])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    beta = json.loads(proposal.proposed_content)["models"][0]
    assert beta["columns"][0]["type"] == "INT"  # not retyped
    assert beta["columns"][0]["description"] == "id col"  # semantics still applied


def test_enrichment_appends_genuinely_new_column() -> None:
    # E4: a new column the overlay introduces is appended (subject to downstream
    # physical validation), while existing columns are kept.
    store = _FakeFileStore(
        [
            _active_file(
                "models/b.json",
                [_model_with_columns("beta", [{"name": "id", "type": "INT"}])],
            )
        ]
    )
    overlay_model = _model_with_columns(
        "beta",
        [
            {"name": "id", "type": "INT"},
            {"name": "region", "type": "VARCHAR", "description": "sales region"},
        ],
    )
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([overlay_model])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    beta = json.loads(proposal.proposed_content)["models"][0]
    assert [col["name"] for col in beta["columns"]] == ["id", "region"]


def test_enrichment_fallback_preserves_columns_across_files() -> None:
    # E4: overlay spans two files (no single owner) -> fallback path. Touched
    # models must still keep their base columns via reconciliation.
    store = _FakeFileStore(
        [
            _active_file(
                "models/a.json",
                [
                    _model_with_columns(
                        "alpha",
                        [{"name": "id", "type": "INT"}, {"name": "k", "type": "TEXT"}],
                    )
                ],
            ),
            _active_file(
                "models/b.json",
                [_model_with_columns("beta", [{"name": "id", "type": "INT"}])],
            ),
        ]
    )
    # Overlay re-emits alpha (omitting "k") and beta -> spans files.
    client = LlmWrenClient(
        _config(),
        FakeModelClient(
            _enrich_payload(
                [
                    _model_with_columns("alpha", [{"name": "id", "type": "INT"}]),
                    _model_with_columns("beta", [{"name": "id", "type": "INT"}]),
                ]
            )
        ),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    models = {m["name"]: m for m in json.loads(proposal.proposed_content)["models"]}
    alpha_columns = [col["name"] for col in models["alpha"]["columns"]]
    assert alpha_columns == ["id", "k"]  # "k" preserved despite the omission
    assert not any(
        "dropped existing column" in warning for warning in proposal.warnings
    )


def test_dropped_columns_helper_detects_a_real_drop() -> None:
    # E5: the detector flags a base column missing from the proposal, and ignores
    # models that are absent from the proposal entirely (they live in other files).
    from superset_ai_agent.integrations.wren.llm_client import _dropped_columns

    base_models = [
        _model_with_columns(
            "beta",
            [{"name": "id", "type": "INT"}, {"name": "amount", "type": "BIGINT"}],
        ),
        _model_with_columns("other", [{"name": "x", "type": "INT"}]),
    ]
    proposed = json.dumps(
        {"models": [_model_with_columns("beta", [{"name": "id", "type": "INT"}])]}
    )

    assert _dropped_columns(base_models, proposed) == ["beta.amount"]
    # "other" is not in the proposal at all -> not reported as dropped.
    assert all(
        not item.startswith("other.")
        for item in _dropped_columns(base_models, proposed)
    )


# --- E2: the prompt sees a trimmed reference, not full re-emittable bodies ----


class SequencedModelClient:
    """ModelClient stub returning a queued sequence of payloads, one per call."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return ModelResult(content=self.contents[index])

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return []


def _payload_sent(messages: list[ChatMessage]) -> dict[str, Any]:
    """Extract the JSON payload from a recorded enrichment user message."""

    return json.loads(messages[-1].content.split("\n", 1)[1])


def test_enrichment_prompt_sends_trimmed_reference_not_full_bodies() -> None:
    # E2 / W4: current_mdl in the prompt is a reference (names, table refs,
    # column name+type+description) — not full bodies (no refSql/properties/
    # isCalculated).
    full_model = {
        "name": "beta",
        "tableReference": {"table": "beta"},
        "refSql": "SELECT * FROM beta",
        "properties": {"owner": "sales"},
        "columns": [
            {"name": "id", "type": "INT", "properties": {"synonym": "identifier"}},
            {"name": "amount", "type": "BIGINT", "description": "amount moved"},
        ],
    }
    store = _FakeFileStore([_active_file("models/b.json", [full_model])])
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([_model("beta", description="x")])),
        mdl_file_store=store,
    )

    client.propose_mdl_from_document(project=_project(), document=_document())

    reference = _payload_sent(client.model_client.calls[0])["current_mdl"]
    beta = reference[0]
    assert beta["name"] == "beta"
    assert beta["tableReference"] == {"table": "beta"}
    assert "refSql" not in beta
    assert "properties" not in beta
    columns = {col["name"]: col for col in beta["columns"]}
    assert set(columns["id"].keys()) <= {"name", "type", "description"}
    assert "properties" not in columns["id"]
    assert columns["amount"]["type"] == "BIGINT"
    assert columns["amount"]["description"] == "amount moved"


# --- E3: authoring correction loop -------------------------------------------


_INVALID_MODEL = {
    "name": "deals",
    "tableReference": {"table": "deals"},
    # Calculated column without an expression: parses, but fails structural
    # validation (calculated_requires_expression) — a clean retry trigger.
    "columns": [{"name": "score", "type": "DOUBLE", "isCalculated": True}],
}
_VALID_MODEL = {
    "name": "deals",
    "tableReference": {"table": "deals"},
    "columns": [{"name": "score", "type": "DOUBLE"}],
}


def test_enrichment_retries_on_invalid_then_succeeds() -> None:
    # E3: first draft is structurally invalid; the loop re-prompts with the errors
    # and the second draft validates. Default config allows one retry.
    model = SequencedModelClient(
        [_enrich_payload([_INVALID_MODEL]), _enrich_payload([_VALID_MODEL])]
    )
    client = LlmWrenClient(_config(), model)

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.validation.valid is True
    assert len(model.calls) == 2
    second_payload = _payload_sent(model.calls[1])
    assert "previous_validation_errors" in second_payload
    assert any(
        "expression" in error.lower()
        for error in second_payload["previous_validation_errors"]
    )


def test_enrichment_no_retry_when_budget_is_zero() -> None:
    # E3: with the retry budget at 0, the invalid first draft is returned as-is
    # (one model call only).
    config = AgentConfig(wren_adapter="llm", wren_modeling_max_correction_retries=0)
    model = SequencedModelClient(
        [_enrich_payload([_INVALID_MODEL]), _enrich_payload([_VALID_MODEL])]
    )
    client = LlmWrenClient(config, model)

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.validation.valid is False
    assert len(model.calls) == 1


# --- C2.1: deep-engine validation inside the correction loop -----------------


def test_full_proposed_manifest_unions_proposed_over_base() -> None:
    from superset_ai_agent.integrations.wren.llm_client import _full_proposed_manifest

    base = [
        {"name": "deals", "columns": [{"name": "old", "type": "INT"}]},
        {"name": "customers", "columns": [{"name": "id", "type": "INT"}]},
    ]
    proposed = {
        "models": [{"name": "deals", "columns": [{"name": "new", "type": "INT"}]}],
        "relationships": [{"name": "r", "models": ["deals", "customers"]}],
    }

    models, relationships = _full_proposed_manifest(base, proposed)

    # Proposed `deals` wins and leads; the untouched `customers` is carried over so
    # the cross-file relationship can resolve.
    assert [m["name"] for m in models] == ["deals", "customers"]
    assert models[0]["columns"] == [{"name": "new", "type": "INT"}]
    assert [r["name"] for r in relationships] == ["r"]


def test_enrichment_deep_validation_repairs_engine_error(monkeypatch) -> None:
    # C2.1: both drafts are structurally valid, but wren-core rejects the first
    # (an expression error). The loop must fold the engine error into the retry and
    # surface the corrected second draft.
    from superset_ai_agent.integrations.wren import llm_client as mod

    monkeypatch.setattr(mod, "wren_core_available", lambda: True)
    deep_calls = {"n": 0}

    def fake_deep(models, relationships):
        deep_calls["n"] += 1
        if deep_calls["n"] == 1:
            return MdlValidationResult(
                valid=False,
                messages=[
                    MdlValidationMessage(
                        message="calculated field 'score' references unknown column",
                        code="wren_core_error",
                    )
                ],
            )
        return MdlValidationResult(valid=True)

    monkeypatch.setattr(mod, "validate_with_wren_core", fake_deep)
    config = AgentConfig(wren_adapter="llm", wren_modeling_deep_validation=True)
    model = SequencedModelClient(
        [_enrich_payload([_VALID_MODEL]), _enrich_payload([_VALID_MODEL])]
    )
    client = LlmWrenClient(config, model)

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.validation.valid is True
    assert deep_calls["n"] == 2  # deep-validated each draft
    assert len(model.calls) == 2  # the engine error forced a retry
    second_payload = _payload_sent(model.calls[1])
    assert any(
        "calculated field" in error.lower()
        for error in second_payload["previous_validation_errors"]
    )


def test_enrichment_skips_deep_validation_when_flag_off(monkeypatch) -> None:
    # Default config: deep validation never runs even with wren-core present.
    from superset_ai_agent.integrations.wren import llm_client as mod

    monkeypatch.setattr(mod, "wren_core_available", lambda: True)
    called = {"n": 0}

    def fake_deep(models, relationships):
        called["n"] += 1
        return MdlValidationResult(valid=False)

    monkeypatch.setattr(mod, "validate_with_wren_core", fake_deep)
    client = LlmWrenClient(_config(), FakeModelClient(_enrich_payload([_VALID_MODEL])))

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.validation.valid is True
    assert called["n"] == 0  # flag off → no deep validation


# --- H5.1: cube entries survive a patch --------------------------------------


def test_cube_merge_preserves_omitted_measures() -> None:
    from superset_ai_agent.integrations.wren.llm_client import (
        _merge_manifest_sections,
    )

    base = {
        "cubes": [
            {
                "name": "sales",
                "baseObject": "deals",
                "measures": [{"name": "m1"}, {"name": "m2"}],
            }
        ]
    }
    overlay = {
        "cubes": [
            {"name": "sales", "measures": [{"name": "m1", "description": "enriched"}]}
        ]
    }

    merged = _merge_manifest_sections(base, overlay)

    cube = merged["cubes"][0]
    assert [measure["name"] for measure in cube["measures"]] == ["m1", "m2"]
    assert cube["baseObject"] == "deals"  # authoritative, preserved
    m1 = next(m for m in cube["measures"] if m["name"] == "m1")
    assert m1["description"] == "enriched"


# --- E2/E3: physical-schema grounding + physical repair ----------------------


_GHOST_MODEL = {
    "name": "deals",
    "tableReference": {"table": "deals"},
    "columns": [
        {"name": "id", "type": "INT"},
        {"name": "ghost", "type": "INT"},  # not in the physical schema
    ],
}
_REAL_MODEL = {
    "name": "deals",
    "tableReference": {"table": "deals"},
    "columns": [{"name": "id", "type": "INT"}],
}


def test_enrichment_prompt_includes_physical_schema() -> None:
    # E2: the authoritative physical schema is handed to the model for grounding.
    model = FakeModelClient(_enrich_payload([_REAL_MODEL]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
        schema={"deals": ["id", "amount"]},
    )

    payload = _payload_sent(model.calls[0])
    assert payload["physical_schema"] == {"deals": ["id", "amount"]}


def test_enrichment_repairs_hallucinated_column_against_schema() -> None:
    # E3: the first draft references a column absent from the physical schema; the
    # loop is told (physical error) and the corrected second draft validates.
    model = SequencedModelClient(
        [_enrich_payload([_GHOST_MODEL]), _enrich_payload([_REAL_MODEL])]
    )
    client = LlmWrenClient(_config(), model)

    proposal = client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
        schema={"deals": ["id"]},
    )

    assert proposal.validation.valid is True
    assert len(model.calls) == 2
    second = _payload_sent(model.calls[1])
    assert any(
        "ghost" in error.lower() for error in second["previous_validation_errors"]
    )


def test_enrichment_without_schema_does_not_send_physical_schema() -> None:
    model = FakeModelClient(_enrich_payload([_REAL_MODEL]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(project=_project(), document=_document())

    assert "physical_schema" not in _payload_sent(model.calls[0])


def test_enrichment_prompt_includes_physical_schema_types() -> None:
    # C3: catalog types are handed to the model so it types new columns correctly.
    model = FakeModelClient(_enrich_payload([_REAL_MODEL]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
        schema={"deals": ["id", "amount"]},
        schema_types={"deals": {"id": "INT", "amount": "BIGINT"}},
    )

    payload = _payload_sent(model.calls[0])
    assert payload["physical_schema_types"] == {
        "deals": {"id": "INT", "amount": "BIGINT"}
    }


def _long_document(extracted_text: str) -> SemanticDocument:
    return SemanticDocument(
        filename="glossary.md",
        content_type="text/markdown",
        size_bytes=len(extracted_text),
        scope=ConversationScope(
            database_id=1, catalog_name=None, schema_name="sales", dataset_ids=[]
        ),
        checksum="abc",
        storage_uri="mem://glossary.md",
        extracted_text=extracted_text,
    )


def test_enrichment_selects_relevant_late_section_within_budget() -> None:
    # C4: a large document with irrelevant head filler and a schema-relevant late
    # section — the prompt keeps the late section instead of a blind head-cut.
    filler = "\n\n".join(f"filler paragraph {i} about nothing" for i in range(40))
    late = "The amount column on deals records the booked value."
    document = _long_document(f"{filler}\n\n{late}")
    config = AgentConfig(wren_adapter="llm", wren_document_prompt_char_budget=150)
    model = FakeModelClient(_enrich_payload([_model("deals")]))
    client = LlmWrenClient(config, model)

    client.propose_mdl_from_document(
        project=_project(),
        document=document,
        schema={"deals": ["amount"]},
    )

    sent_text = _payload_sent(model.calls[0])["document_text"]
    assert "amount column on deals" in sent_text  # relevant late section survived
    assert len(sent_text) <= 150  # budgeted


def test_enrichment_small_document_sent_whole() -> None:
    # A document within budget is unchanged (no chunking surprises).
    document = _long_document("Gross moves means total units shifted.")
    model = FakeModelClient(_enrich_payload([_model("deals")]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(project=_project(), document=document)

    assert (
        _payload_sent(model.calls[0])["document_text"]
        == "Gross moves means total units shifted."
    )


def test_enrichment_omits_physical_schema_types_when_absent() -> None:
    # Names-only schema (snapshot path) → no types key, grounding degrades to E2.
    model = FakeModelClient(_enrich_payload([_REAL_MODEL]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
        schema={"deals": ["id", "amount"]},
    )

    assert "physical_schema_types" not in _payload_sent(model.calls[0])


def test_enrichment_prompt_includes_instructions() -> None:
    model = FakeModelClient(_enrich_payload([_model("deals")]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
        instructions=["Prefer gross over net", "Name metrics in snake_case"],
    )

    payload = _payload_sent(model.calls[0])
    assert payload["instructions"] == [
        "Prefer gross over net",
        "Name metrics in snake_case",
    ]


def test_enrichment_omits_instructions_when_none() -> None:
    model = FakeModelClient(_enrich_payload([_model("deals")]))
    client = LlmWrenClient(_config(), model)

    client.propose_mdl_from_document(project=_project(), document=_document())

    assert "instructions" not in _payload_sent(model.calls[0])


def test_enrichment_grounds_on_draft_base_before_activation() -> None:
    # CR1: onboarding writes drafts; enrichment must overlay onto the draft base
    # without a manual activation in between. The draft 'deals' is the patch target.
    store = _FakeFileStore([_draft_file("models/deals.json", [_model("deals")])])
    client = LlmWrenClient(
        _config(),
        FakeModelClient(_enrich_payload([_model("deals", description="enriched")])),
        mdl_file_store=store,
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert proposal.proposed_path == "models/deals.json"
    merged = json.loads(proposal.proposed_content)
    assert merged["models"][0]["description"] == "enriched"


def test_enrichment_prefers_active_over_draft_for_same_path() -> None:
    # CR1: when both an active and a draft exist for a path, the active wins as base.
    store = _FakeFileStore(
        [
            _draft_file("models/deals.json", [_model("deals", description="stale")]),
            _active_file("models/deals.json", [_model("deals", description="live")]),
        ]
    )
    client = LlmWrenClient(
        _config(), FakeModelClient(json.dumps({"files": []})), mdl_file_store=store
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    # No-op proposal echoes the *active* base, not the stale draft.
    assert json.loads(proposal.proposed_content)["models"][0]["description"] == "live"


def test_no_change_proposal_when_provider_empty_and_base_exists() -> None:
    # CR2: with a real base, an empty provider response must NOT fabricate a
    # schema-named blob; it echoes the base unchanged with a loud warning.
    store = _FakeFileStore([_draft_file("models/deals.json", [_model("deals")])])
    client = LlmWrenClient(
        _config(), FakeModelClient(json.dumps({"files": []})), mdl_file_store=store
    )

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    parsed = json.loads(proposal.proposed_content)
    names = [m["name"] for m in parsed["models"]]
    assert names == ["deals"]  # echoed base — not a "sales" schema-name blob
    assert "sales" not in names
    assert any("no enrichment changes" in w.lower() for w in proposal.warnings)


def test_bare_project_still_degrades_to_deterministic_draft() -> None:
    # CR2: with no base and no physical schema there is nothing to anchor to, so the
    # honest structure-only degrade (with the provider-fallback warning) still applies.
    client = LlmWrenClient(_config(), FakeModelClient("not json at all"))

    proposal = client.propose_mdl_from_document(
        project=_project(), document=_document()
    )

    assert any("structured" in w.lower() for w in proposal.warnings)


def test_split_schema_view_separates_tables_and_types() -> None:
    from superset_ai_agent.integrations.wren.llm_client import _split_schema_view

    view = {
        "sales": {"orders": {"columns": ["id"], "types": {"id": "BIGINT"}}},
        "crm": {"customers": {"columns": ["id", "name"]}},
    }
    tables, types = _split_schema_view(view)

    assert tables == {"sales": {"orders": ["id"]}, "crm": {"customers": ["id", "name"]}}
    assert types == {"sales": {"orders": {"id": "BIGINT"}}}  # crm has no types
    assert _split_schema_view(None) == (None, None)
