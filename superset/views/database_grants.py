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
"""SPA-routing view for the Database Access Grants admin panel."""

from flask_appbuilder import expose
from flask_appbuilder.security.decorators import has_access, permission_name

from superset.superset_typing import FlaskResponse
from superset.views.base import BaseSupersetView


class DatabaseAccessGrantsView(BaseSupersetView):
    route_base = "/databaseaccessgrants"
    # Piggyback the REST API's permission (can_read on DatabaseAccessGrant,
    # Admin-only via ADMIN_ONLY_VIEW_MENUS) instead of minting a second,
    # class-name-derived permission — same pattern as RowLevelSecurityView.
    class_permission_name = "DatabaseAccessGrant"

    @expose("/list/")
    @has_access
    @permission_name("read")
    def list(self) -> FlaskResponse:
        return super().render_app_template()
