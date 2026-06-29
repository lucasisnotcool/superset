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

from typing import Any
from unittest.mock import MagicMock, patch

from sqlalchemy.orm.session import Session

from superset import db


def test_put_invalid_dataset(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    Test invalid payloads.
    """
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database

    SqlaTable.metadata.create_all(db.session.get_bind())

    database = Database(
        database_name="my_db",
        sqlalchemy_uri="sqlite://",
    )
    dataset = SqlaTable(
        table_name="test_put_invalid_dataset",
        database=database,
    )
    db.session.add(dataset)
    db.session.flush()

    response = client.put(
        "/api/v1/dataset/1",
        json={"invalid": "payload"},
    )
    assert response.status_code == 422
    assert response.json == {
        "errors": [
            {
                "message": "The schema of the submitted payload is invalid.",
                "error_type": "MARSHMALLOW_ERROR",
                "level": "error",
                "extra": {
                    "messages": {"invalid": ["Unknown field."]},
                    "payload": {"invalid": "payload"},
                    "issue_codes": [
                        {
                            "code": 1040,
                            "message": (
                                "Issue 1040 - The submitted payload failed validation."
                            ),
                        }
                    ],
                },
            }
        ]
    }


def test_get_dataset_include_rendered_sql_passes_table_to_template_processor(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    Dataset API: Test that include_rendered_sql passes the table
    to get_template_processor.

    Regression test for the bug where get_template_processor was called without
    the `table` argument, leaving self._schema as None in processors like
    PrestoTemplateProcessor and causing NPEs when templates reference partition
    functions without an explicit schema.
    """
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database

    SqlaTable.metadata.create_all(db.session.get_bind())

    database = Database(
        database_name="my_db",
        sqlalchemy_uri="sqlite://",
    )
    dataset = SqlaTable(
        table_name="test_render_sql_table",
        schema="my_schema",
        database=database,
        sql="SELECT 1",
    )
    db.session.add(dataset)
    db.session.flush()

    mock_processor = MagicMock()
    mock_processor.process_template.return_value = "SELECT 1"

    with patch(
        "superset.datasets.api.get_template_processor",
        return_value=mock_processor,
    ) as mock_get_processor:
        response = client.get(
            f"/api/v1/dataset/{dataset.id}?include_rendered_sql=true",
        )

    assert response.status_code == 200
    mock_get_processor.assert_called_once_with(database=database, table=dataset)


def test_handle_filters_args_returns_request_scoped_filters(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    ``_handle_filters_args`` must return a fresh ``Filters`` instance per
    call so concurrent requests don't share filter state.

    Regression test for #33828: under concurrent traffic the FAB default
    implementation mutates ``self._filters`` (a single shared instance),
    causing filters from one request to leak into another.

    The fix lives on ``BaseSupersetModelRestApi`` so every superset REST
    API subclass (datasets, charts, dashboards, saved queries, etc.)
    inherits the request-scoped behavior. This test exercises it via
    ``DatasetRestApi`` as a concrete subclass.
    """
    from flask_appbuilder.const import API_FILTERS_RIS_KEY

    from superset.datasets.api import DatasetRestApi

    api = DatasetRestApi()
    api.datamodel = MagicMock()
    api.search_columns = ["table_name"]
    api.search_filters = {}
    api._base_filters = MagicMock()  # noqa: SLF001

    # Each call should construct a fresh Filters instance via datamodel.get_filters
    rison_args = {
        API_FILTERS_RIS_KEY: [{"col": "table_name", "opr": "eq", "value": "a"}],
    }
    api._handle_filters_args(rison_args)  # noqa: SLF001
    api._handle_filters_args(rison_args)  # noqa: SLF001

    assert api.datamodel.get_filters.call_count == 2
    # Returned object must be the joined-filters result of the *fresh* Filters,
    # not the shared self._filters attribute.
    fresh_filters = api.datamodel.get_filters.return_value
    assert fresh_filters.rest_add_filters.call_count == 2
    assert fresh_filters.get_joined_filters.call_count == 2


def _make_dataset(table_name: str = "etag_table") -> Any:
    """Persist a minimal dataset (with one column) and return it."""
    from superset.connectors.sqla.models import SqlaTable, TableColumn
    from superset.models.core import Database

    SqlaTable.metadata.create_all(db.session.get_bind())
    database = Database(database_name="etag_db", sqlalchemy_uri="sqlite://")
    dataset = SqlaTable(
        table_name=table_name,
        database=database,
        columns=[TableColumn(column_name="col_a")],
    )
    db.session.add(dataset)
    db.session.flush()
    return dataset


def test_get_dataset_returns_etag_and_304_on_revalidation(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    Dataset detail GET sets a strong ETag, and an ``If-None-Match`` revalidation
    of the same (unchanged) dataset returns 304 with no body — letting the client
    skip the full columns/metrics serialization. (Track A2)
    """
    dataset = _make_dataset()

    first = client.get(f"/api/v1/dataset/{dataset.id}")
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag, "detail response must carry an ETag"
    assert first.headers["Cache-Control"].replace(" ", "").find("private") != -1

    revalidate = client.get(
        f"/api/v1/dataset/{dataset.id}",
        headers={"If-None-Match": etag},
    )
    assert revalidate.status_code == 304
    assert revalidate.get_data(as_text=True) == ""
    assert revalidate.headers.get("ETag") == etag


def test_get_dataset_etag_changes_after_column_edit(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    A child (column) change invalidates the ETag even though it need not bump the
    parent's ``changed_on`` — so the prior tag no longer 304-matches and the
    client refetches. Guards risk A-R2 (stale schema). (Track A2)
    """
    from superset.connectors.sqla.models import TableColumn

    dataset = _make_dataset("etag_child_table")

    first = client.get(f"/api/v1/dataset/{dataset.id}")
    assert first.status_code == 200
    etag = first.headers["ETag"]

    # Add a column (a child change). The detail ETag folds in columns'
    # changed_on, so the stale tag must NOT 304-match anymore.
    dataset.columns.append(TableColumn(column_name="col_b"))
    db.session.flush()

    revalidate = client.get(
        f"/api/v1/dataset/{dataset.id}",
        headers={"If-None-Match": etag},
    )
    assert revalidate.status_code == 200
    assert revalidate.headers["ETag"] != etag


def test_get_dataset_passes_eager_load_options(
    session: Session,
    client: Any,
    full_api_access: None,
) -> None:
    """
    The detail GET resolves the dataset through ``find_by_id_or_uuid`` with eager
    loader ``options`` (columns, metrics, owners, database) so a wide dataset
    doesn't fan out into per-relationship lazy loads. (Track A1)
    """
    from superset.daos.dataset import DatasetDAO

    dataset = _make_dataset("eager_table")

    real_find = DatasetDAO.find_by_id_or_uuid
    captured: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_find(*args, **kwargs)

    with patch.object(DatasetDAO, "find_by_id_or_uuid", side_effect=spy):
        response = client.get(f"/api/v1/dataset/{dataset.id}")

    assert response.status_code == 200
    options = captured.get("options")
    assert options is not None
    assert len(options) == 4
