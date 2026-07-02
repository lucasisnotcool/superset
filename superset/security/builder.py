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

Wire via ``CUSTOM_SECURITY_MANAGER = BuilderSecurityManager`` in
``superset_config.py``. The role is (re)computed on every
``sync_role_definitions`` (``superset init``), so grants must live here, not
in ad-hoc Roles-UI edits, which a sync would overwrite.
"""

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

    def _is_builder_pvm(self, pvm: PermissionView) -> bool:
        """
        Return True if the FAB permission/view belongs to the Builder role.

        Builder = Gamma ∪ sql_lab ∪ {write perms on Database}. None of these
        branches include ``all_database_access`` / ``all_datasource_access``
        (both are Alpha-only), which owner-scoping of connections depends on;
        the explicit guard makes that invariant hold even if a subclass
        loosens a branch.

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
        )

    def sync_role_definitions(self) -> None:
        """Sync built-in roles, then (re)compute the Builder role."""
        super().sync_role_definitions()
        self.set_role(BUILDER_ROLE_NAME, self._is_builder_pvm, self._get_all_pvms())
        self.session.commit()
