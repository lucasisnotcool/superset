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


def test_inmemory_snapshot_round_trips_tables_by_schema() -> None:
    store = InMemorySchemaSnapshotStore()
    store.upsert(
        SchemaSnapshot(
            project_id="p1",
            tables={"orders": ["id"]},
            tables_by_schema={
                "sales": {"orders": ["id"]},
                "crm": {"customers": ["id"]},
            },
        )
    )
    got = store.get("p1")
    assert got is not None
    assert got.tables_by_schema == {
        "sales": {"orders": ["id"]},
        "crm": {"customers": ["id"]},
    }


def test_sqlalchemy_snapshot_round_trips_tables_by_schema() -> None:
    # Exercises the new nullable column end-to-end through SQLAlchemy (F3).
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from superset_ai_agent.persistence.models import Base
    from superset_ai_agent.semantic_layer.schema_snapshot import (
        SqlAlchemySchemaSnapshotStore,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    store = SqlAlchemySchemaSnapshotStore(sessionmaker(bind=engine))

    store.upsert(
        SchemaSnapshot(
            project_id="p1",
            schema_name="sales",
            tables={"orders": ["id"]},
            tables_by_schema={
                "sales": {"orders": ["id"]},
                "crm": {"customers": ["id"]},
            },
        )
    )
    got = store.get("p1")
    assert got is not None
    assert got.tables_by_schema == {
        "sales": {"orders": ["id"]},
        "crm": {"customers": ["id"]},
    }
    # A single-schema snapshot leaves it empty (degrades closed).
    store.upsert(SchemaSnapshot(project_id="p2", tables={"orders": ["id"]}))
    assert store.get("p2").tables_by_schema == {}


def test_outage_fallback_index_is_schema_qualified() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    snap = SchemaSnapshot(
        project_id="p1",
        tables={"orders": ["id"], "customers": ["id"]},
        tables_by_schema={"sales": {"orders": ["id"]}, "crm": {"customers": ["id"]}},
    )
    index = SchemaIndex.from_snapshot(
        snap.tables, tables_by_schema=snap.tables_by_schema or None
    )
    assert index.is_multi_schema()
    assert index.has_table("customers", "crm")
    assert not index.has_table("customers", "sales")  # qualified, not flat
