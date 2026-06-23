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
                    MetricSummary(
                        name="total_moves", expression="SUM(gross_moves)"
                    )
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
    assert any(
        "structured" in warning.lower() for warning in proposals[0].warnings
    )


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

    proposal = client.propose_mdl_from_document(
        project=_project(), document=document
    )

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
                "relationships": [
                    {"name": "deals_site", "join_type": "MANY_TO_ONE"}
                ],
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


def test_factory_returns_llm_client_with_model_client() -> None:
    client = create_wren_client(_config(), model_client=FakeModelClient("{}"))
    assert isinstance(client, LlmWrenClient)


def test_factory_falls_back_without_model_client() -> None:
    client = create_wren_client(_config(), model_client=None)
    assert not isinstance(client, LlmWrenClient)
