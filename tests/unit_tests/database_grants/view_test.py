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

from pytest_mock import MockerFixture


def test_grants_view_is_registered_and_gated(client: Any) -> None:
    """The SPA view exists and an unauthenticated request is bounced to
    login (has_access), not served."""
    response = client.get("/databaseaccessgrants/list/")
    assert response.status_code in (302, 401)
    if response.status_code == 302:
        assert "/login" in response.headers["Location"]


def test_grants_view_serves_spa_when_permitted(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    # The unit harness has no built spa.html template; assert the route
    # dispatches into the SPA renderer rather than being blocked. Patch via
    # the view's own module reference — the app fixture reloads
    # superset.views.base, so the registered class predates that reload.
    # The route calls super().render_app_template(), so patch the parent
    # class through this module's own import (super() resolves on that exact
    # class object, unaffected by the harness's reload of superset.views.base).
    render = mocker.patch(
        "superset.views.database_grants.BaseSupersetView.render_app_template",
        return_value="ok",
    )
    response = client.get("/databaseaccessgrants/list/")
    assert response.status_code == 200
    render.assert_called_once()
