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
"""Tests for the Builder role PVM predicate (self-service connections)."""

from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from superset.security.builder import BuilderSecurityManager
from superset.security.manager import SupersetSecurityManager


def make_pvm(permission: str, view_menu: str) -> MagicMock:
    pvm = MagicMock()
    pvm.permission.name = permission
    pvm.view_menu.name = view_menu
    return pvm


@pytest.fixture
def sm() -> BuilderSecurityManager:
    # The predicate methods only touch class-level constants and sibling
    # predicates, so an uninitialized instance is sufficient (constructing a
    # real security manager requires a full appbuilder).
    return BuilderSecurityManager.__new__(BuilderSecurityManager)


@pytest.mark.parametrize(
    "permission,view_menu",
    [
        # Self-service connection CRUD (all REST mutations map to can_write)
        ("can_write", "Database"),
        ("can_export", "Database"),
        # Read side arrives via the Gamma baseline
        ("can_read", "Database"),
        # SQL Lab bundle (which also unlocks the AI-agent panels)
        ("can_sqllab", "Superset"),
        ("can_execute_sql_query", "SQLLab"),
        ("can_get_results", "SQLLab"),
        ("can_read", "SQLLab"),
        ("menu_access", "SQL Lab"),
        ("can_read", "SavedQuery"),
        ("can_write", "SavedQuery"),
        # Self-scoped database-grant endpoints (notification dialog)
        ("can_mine", "DatabaseAccessGrant"),
        ("can_acknowledge", "DatabaseAccessGrant"),
    ],
)
def test_builder_includes(
    sm: BuilderSecurityManager, permission: str, view_menu: str
) -> None:
    assert sm._is_builder_pvm(make_pvm(permission, view_menu)) is True


@pytest.mark.parametrize(
    "permission,view_menu",
    [
        # R4: the invariant owner-scoping depends on. A Builder holding
        # all_database_access would bypass DatabaseFilter entirely.
        ("all_database_access", "all_database_access"),
        ("all_datasource_access", "all_datasource_access"),
        ("all_query_access", "all_query_access"),
        # Admin-only stays admin-only
        ("can_update_role", "Superset"),
        ("can_grant_guest_token", "SecurityRestApi"),
        ("menu_access", "Row Level Security"),
        # Alpha-only stays out
        ("muldelete", "Database"),
        ("can_upload", "Database"),
        # Object-level grants are user-defined, never role-baked
        ("database_access", "[my_db].(id:42)"),
        ("datasource_access", "[my_db].[table](id:1)"),
        ("schema_access", "[my_db].[public]"),
        # Grant MANAGEMENT stays Admin-only: only the self-scoped
        # mine/acknowledge endpoints are Builder-accessible.
        ("can_read", "DatabaseAccessGrant"),
        ("can_write", "DatabaseAccessGrant"),
        ("menu_access", "Database Access Grants"),
    ],
)
def test_builder_excludes(
    sm: BuilderSecurityManager, permission: str, view_menu: str
) -> None:
    assert sm._is_builder_pvm(make_pvm(permission, view_menu)) is False


def test_builder_never_gets_alpha_only_permissions(
    sm: BuilderSecurityManager,
) -> None:
    """Every ALPHA_ONLY_PERMISSIONS name is rejected on any view menu."""
    for permission in sm.ALPHA_ONLY_PERMISSIONS:
        assert sm._is_builder_pvm(make_pvm(permission, "Database")) is False
        assert sm._is_builder_pvm(make_pvm(permission, permission)) is False


def test_gamma_never_gets_grant_endpoints(sm: BuilderSecurityManager) -> None:
    """DatabaseAccessGrant is in ADMIN_ONLY_VIEW_MENUS, so the Gamma
    predicate must reject ALL perms on it — otherwise every Gamma user would
    inherit grant management. Builder re-adds only mine/acknowledge."""
    for permission in ("can_read", "can_write", "can_mine", "can_acknowledge"):
        assert sm._is_gamma_pvm(make_pvm(permission, "DatabaseAccessGrant")) is False
    assert sm._is_gamma_pvm(make_pvm("menu_access", "Database Access Grants")) is False


def test_on_user_login_chains_super_then_claims(
    sm: BuilderSecurityManager, mocker: MockerFixture
) -> None:
    super_hook = mocker.patch.object(SupersetSecurityManager, "on_user_login")
    claim = mocker.patch(
        "superset.commands.database_grants.claim.claim_database_grants"
    )
    user = MagicMock()

    sm.on_user_login(user)

    super_hook.assert_called_once_with(user)
    claim.assert_called_once_with(user)


def test_add_user_claims_grants_for_created_user(
    sm: BuilderSecurityManager, mocker: MockerFixture
) -> None:
    created = MagicMock()
    mocker.patch.object(SupersetSecurityManager, "add_user", return_value=created)
    claim = mocker.patch(
        "superset.commands.database_grants.claim.claim_database_grants"
    )

    result = sm.add_user("alice@example.com", "Alice", "Doe", "alice@example.com")

    assert result is created
    claim.assert_called_once_with(created)


def test_add_user_skips_claim_when_creation_fails(
    sm: BuilderSecurityManager, mocker: MockerFixture
) -> None:
    mocker.patch.object(SupersetSecurityManager, "add_user", return_value=None)
    claim = mocker.patch(
        "superset.commands.database_grants.claim.claim_database_grants"
    )

    assert sm.add_user("alice@example.com", "Alice", "Doe", "x@y.z") is None
    claim.assert_not_called()


def test_builder_is_superset_of_gamma_and_sql_lab(
    sm: BuilderSecurityManager,
) -> None:
    """Builder must include everything Gamma and sql_lab would include."""
    samples = [
        ("can_read", "Chart"),
        ("can_write", "Chart"),
        ("can_read", "Dashboard"),
        ("can_write", "Dashboard"),
        ("can_read", "Dataset"),
        ("can_explore", "Superset"),
        ("can_get_results", "SQLLab"),
        ("can_activate", "TabStateView"),
        ("can_export_csv", "SQLLab"),
    ]
    for permission, view_menu in samples:
        pvm = make_pvm(permission, view_menu)
        if sm._is_gamma_pvm(pvm) or sm._is_sql_lab_pvm(pvm):
            assert sm._is_builder_pvm(pvm) is True, (permission, view_menu)
