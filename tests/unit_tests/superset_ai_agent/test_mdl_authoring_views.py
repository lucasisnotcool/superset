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

"""G1 — the authoring contract can emit views (semantic and native).

These pin that ``AuthoredManifest`` carries views, that the JSON schema handed to
the model exposes them, and that a serialized view file validates structurally.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.mdl_authoring import (
    AuthoredManifest,
    AuthoredView,
    MdlProposalResponse,
    proposal_response_schema,
    ProposedMdlFile,
    serialize_manifest,
)
from superset_ai_agent.semantic_layer.mdl_schema import MdlView
from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl


def test_proposal_response_schema_exposes_views() -> None:
    # The model can only emit a view if the format_schema advertises one. The
    # schema is derived from the pydantic model, so this guards the wiring.
    schema = json.dumps(proposal_response_schema())
    assert "views" in schema
    assert "AuthoredView" in schema


def test_authored_view_round_trips_through_proposal_response() -> None:
    response = MdlProposalResponse(
        files=[
            ProposedMdlFile(
                path="views/big_orders.json",
                manifest=AuthoredManifest(
                    views=[
                        AuthoredView(
                            name="big_orders",
                            statement="SELECT id, amount FROM orders WHERE amount > 1",
                            properties={"description": "Orders over 1"},
                        )
                    ]
                ),
            )
        ]
    )
    payload = response.model_dump(by_alias=True)
    reparsed = MdlProposalResponse.model_validate(payload)
    view = reparsed.files[0].manifest.views[0]
    assert view.name == "big_orders"
    assert view.dialect is None


def test_serialized_semantic_view_file_validates() -> None:
    content = serialize_manifest(
        AuthoredManifest(
            views=[
                AuthoredView(
                    name="big_orders",
                    statement="SELECT id FROM orders",
                )
            ]
        )
    )
    parsed = json.loads(content)
    assert parsed["views"][0]["name"] == "big_orders"
    # exclude_none keeps the file lean: an unset dialect is not emitted.
    assert "dialect" not in parsed["views"][0]
    result = validate_mdl(content)
    assert result.valid is True


def test_native_view_carries_dialect_through_serialization() -> None:
    content = serialize_manifest(
        AuthoredManifest(
            views=[
                AuthoredView(
                    name="legacy_rollup",
                    statement="SELECT * FROM public.raw_rollup_v2",
                    dialect="postgres",
                )
            ]
        )
    )
    parsed = json.loads(content)
    assert parsed["views"][0]["dialect"] == "postgres"


def test_mdl_view_schema_accepts_optional_dialect() -> None:
    semantic = MdlView(name="v", statement="SELECT 1 FROM orders")
    native = MdlView(name="v2", statement="SELECT 1", dialect="bigquery")
    assert semantic.dialect is None
    assert native.dialect == "bigquery"
