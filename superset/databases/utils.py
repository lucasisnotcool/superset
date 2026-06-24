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

import logging
from typing import Any, Callable, TYPE_CHECKING, TypeVar

from sqlalchemy.engine.url import make_url, URL

from superset.commands.database.exceptions import DatabaseInvalidError
from superset.sql.parse import Table

if TYPE_CHECKING:
    from superset.databases.schemas import (
        TableMetadataColumnsResponse,
        TableMetadataForeignKeysIndexesResponse,
        TableMetadataResponse,
    )

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _best_effort(
    section: Callable[[], T],
    default: T,
    warnings: list[str],
    label: str,
) -> T:
    """Run an auxiliary introspection step, degrading to ``default`` on failure.

    A table's columns are essential (a failure there propagates and surfaces as a
    structured error), but the auxiliary sections — primary key, foreign keys,
    indexes, ``SELECT *`` — are best-effort: a single failing one (e.g. an engine
    that can't compile a partition-aware ``SELECT *``, or flaky FK reflection)
    should not fail the whole request. The detail is logged; a concise,
    non-sensitive note is added to ``warnings`` so the caller can surface it.
    """

    try:
        return section()
    except Exception:  # pylint: disable=broad-except
        logger.warning("Could not load %s for table metadata", label, exc_info=True)
        warnings.append(f"Could not load {label}.")
        return default


def get_foreign_keys_metadata(
    database: Any,
    table: Table,
) -> list[TableMetadataForeignKeysIndexesResponse]:
    foreign_keys = database.get_foreign_keys(table)
    for fk in foreign_keys:
        fk["column_names"] = fk.pop("constrained_columns")
        fk["type"] = "fk"
    return foreign_keys


def get_indexes_metadata(
    database: Any,
    table: Table,
) -> list[TableMetadataForeignKeysIndexesResponse]:
    indexes = database.get_indexes(table)
    for idx in indexes:
        idx["type"] = "index"
    return indexes


def get_col_type(col: dict[Any, Any]) -> str:
    try:
        dtype = f"{col['type']}"
    except Exception:  # pylint: disable=broad-except
        # sqla.types.JSON __str__ has a bug, so using __class__.
        dtype = col["type"].__class__.__name__
    return dtype


def get_table_metadata(database: Any, table: Table) -> TableMetadataResponse:
    """
    Get table metadata information, including type, pk, fks.

    Column reflection is essential and propagates on failure (e.g. an invalid
    schema), so the caller can surface a structured error. The auxiliary sections
    (primary key, foreign keys, indexes, ``SELECT *``) are best-effort: a single
    failing one degrades to an empty default and is reported via ``warnings``
    rather than failing the whole request.

    :param database: The database model
    :param table: Table instance
    :return: Dict table metadata ready for API response
    """
    keys = []
    warnings: list[str] = []
    columns = database.get_columns(table)
    primary_key: Any = _best_effort(
        lambda: database.get_pk_constraint(table), {}, warnings, "primary key"
    )
    if primary_key and primary_key.get("constrained_columns"):
        primary_key["column_names"] = primary_key.pop("constrained_columns")
        primary_key["type"] = "pk"
        keys += [primary_key]
    foreign_keys: list[TableMetadataForeignKeysIndexesResponse] = _best_effort(
        lambda: get_foreign_keys_metadata(database, table),
        [],
        warnings,
        "foreign keys",
    )
    indexes: list[TableMetadataForeignKeysIndexesResponse] = _best_effort(
        lambda: get_indexes_metadata(database, table), [], warnings, "indexes"
    )
    keys += foreign_keys + indexes
    payload_columns: list[TableMetadataColumnsResponse] = []
    table_comment = _best_effort(
        lambda: database.get_table_comment(table), None, warnings, "table comment"
    )
    for col in columns:
        dtype = get_col_type(col)
        payload_columns.append(
            {
                "name": col["column_name"],
                "type": dtype.split("(")[0] if "(" in dtype else dtype,
                "longType": dtype,
                "keys": [k for k in keys if col["column_name"] in k["column_names"]],
                "comment": col.get("comment"),
            }
        )
    select_star = _best_effort(
        lambda: database.select_star(
            table,
            show_cols=True if columns else False,
            indent=True,
            cols=columns,
            latest_partition=True,
        ),
        "",
        warnings,
        "SELECT * statement",
    )
    return {
        "name": table.table,
        "columns": payload_columns,
        "selectStar": select_star,
        "primaryKey": primary_key,
        "foreignKeys": foreign_keys,
        "indexes": keys,
        "comment": table_comment,
        "warnings": warnings,
    }


def make_url_safe(raw_url: str | URL) -> URL:
    """
    Wrapper for SQLAlchemy's make_url(), which tends to raise too detailed of
    errors, which inevitably find their way into server logs. ArgumentErrors
    tend to contain usernames and passwords, which makes them non-log-friendly
    :param raw_url:
    :return:
    """

    if isinstance(raw_url, str):
        url = raw_url.strip()
        try:
            return make_url(url)  # noqa
        except Exception as ex:
            raise DatabaseInvalidError() from ex

    else:
        return raw_url
