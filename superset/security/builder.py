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
"""Security manager providing a self-service ``Builder`` role.

Builder = Gamma baseline + SQL Lab bundle + write access to the ``Database``
model, so non-admin users can bring their own database connections and use
SQL Lab (which also hosts the AI-agent MDL Lab / Copilot / AI SQL surfaces).

Builder deliberately never receives ``all_database_access`` (or any other
Alpha/Admin-only grant): connection visibility for Builders is owner-scoped
by ``DatabaseFilter``, which privileged principals bypass. Granting
``all_database_access`` to Builder would silently disable that scoping.

This manager also hosts the claim triggers for admin pre-approved database
access grants (see ``superset.commands.database_grants.claim``): grants are
claimed on every successful login (``on_user_login``, which FAB invokes from
all auth paths) and at user creation (``add_user``, which covers SSO
auto-registration). Both claims are idempotent — required because OAuth's
``AUTH_ROLES_SYNC_AT_LOGIN`` overwrites ``user.roles`` from the IdP mapping
on each login — and fail-soft, so they can never break authentication.

Wire via ``CUSTOM_SECURITY_MANAGER = BuilderSecurityManager`` in
``superset_config.py``. The role is (re)computed on every
``sync_role_definitions`` (``superset init``), so grants must live here, not
in ad-hoc Roles-UI edits, which a sync would overwrite.
"""

from typing import Any

from flask_appbuilder.security.sqla.models import PermissionView

from superset.security.manager import SupersetSecurityManager

BUILDER_ROLE_NAME = "Builder"


class BuilderSecurityManager(SupersetSecurityManager):
    """Adds the ``Builder`` self-service role to the built-in role sync."""

    #: Write-side permissions on the ``Database`` model view granted to
    #: Builder so users can create/edit/delete/test their own connections.
    #: Read-side permissions arrive via the Gamma baseline; mutations on the
    #: REST API all map to ``can_write`` (``MODEL_API_RW_METHOD_PERMISSION_MAP``).
    #: ``can_export`` is included so users can export their own connection
    #: (payloads carry masked credentials only); ``can_upload`` stays
    #: Alpha-only.
    DATABASE_WRITE_PERMS = {"can_write", "can_export"}

    #: Self-scoped grant endpoints every Builder needs: ``mine`` (list + lazily
    #: claim your own grants, drives the notification dialog) and
    #: ``acknowledge`` (dismiss the dialog). The ``DatabaseAccessGrant`` view
    #: menu is otherwise Admin-only (``ADMIN_ONLY_VIEW_MENUS``), which keeps
    #: grant management — list/create/revoke — away from Builders.
    GRANT_SELF_PERMS = {"can_mine", "can_acknowledge"}

    def _is_builder_pvm(self, pvm: PermissionView) -> bool:
        """
        Return True if the FAB permission/view belongs to the Builder role.

        Builder = Gamma ∪ sql_lab ∪ {write perms on Database} ∪ {self-scoped
        grant endpoints}. None of these branches include
        ``all_database_access`` / ``all_datasource_access`` (both are
        Alpha-only), which owner-scoping of connections depends on; the
        explicit guard makes that invariant hold even if a subclass loosens a
        branch.

        :param pvm: The FAB permission/view
        :returns: Whether the FAB object is Builder related
        """
        if pvm.permission.name in self.ALPHA_ONLY_PERMISSIONS:
            return False
        return (
            self._is_gamma_pvm(pvm)
            or self._is_sql_lab_pvm(pvm)
            or (
                pvm.view_menu.name == "Database"
                and pvm.permission.name in self.DATABASE_WRITE_PERMS
            )
            or (
                pvm.view_menu.name == "DatabaseAccessGrant"
                and pvm.permission.name in self.GRANT_SELF_PERMS
            )
        )

    def sync_role_definitions(self) -> None:
        """Sync built-in roles, then (re)compute the Builder role."""
        super().sync_role_definitions()
        self.set_role(BUILDER_ROLE_NAME, self._is_builder_pvm, self._get_all_pvms())
        self.session.commit()

    def on_user_login(self, user: Any) -> None:
        """Claim pre-approved database grants on every successful login.

        FAB invokes this hook from ``update_user_auth_stat`` on all auth
        paths (DB, LDAP, OAuth, remote-user, SAML), after any
        ``AUTH_ROLES_SYNC_AT_LOGIN`` roles overwrite — so re-claiming here
        also re-attaches grant roles the IdP sync stripped.
        """
        super().on_user_login(user)
        # pylint: disable=import-outside-toplevel
        from superset.commands.database_grants.claim import claim_database_grants

        claim_database_grants(user)

    def add_user(self, *args: Any, **kwargs: Any) -> Any:
        """Claim pre-approved database grants at user creation.

        Covers SSO auto-registration (``auth_user_oauth`` /
        ``auth_user_remote_user`` with ``AUTH_USER_REGISTRATION``) and manual
        self-registration, so a pre-approved user has access from their very
        first session.
        """
        user = super().add_user(*args, **kwargs)
        if user:
            # pylint: disable=import-outside-toplevel
            from superset.commands.database_grants.claim import claim_database_grants

            claim_database_grants(user)
        return user
