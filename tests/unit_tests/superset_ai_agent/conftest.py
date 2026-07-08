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
"""Shared fixtures for the standalone-agent unit tests."""

from __future__ import annotations

import pytest

from superset_ai_agent.context.superset_metadata import reset_names_listing_cache


@pytest.fixture(autouse=True)
def _fresh_names_listing_cache():
    """The names-listing cache is process-level; isolate it per test.

    Without this, one test's cached (database, catalog, schema) listing leaks
    into the next test that reuses the same scope key with a different fake
    client.
    """

    reset_names_listing_cache()
    yield
    reset_names_listing_cache()
