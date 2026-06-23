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

"""Shared gate for tests that exercise the live wren-core engine.

``requires_wren_core`` stacks two things on a test:

* ``@pytest.mark.requires_wren_core`` — a selectable marker. The AI-agent CI job
  (``.github/workflows/superset-ai-agent.yml``) installs the wheel, asserts it
  imports, then runs ``pytest -m requires_wren_core`` and fails if any of these
  *skip*. That turns a silently-dropped engine test into a loud CI failure.
* ``pytest.mark.skipif(not wren_core_available())`` — so the same tests skip
  cleanly on a developer machine where the optional wheel is absent.

Use this single decorator on every present-path engine test instead of an inline
``skipif`` so the CI gate and the local skip stay in lockstep.
"""

from __future__ import annotations

import pytest

from superset_ai_agent.semantic_layer.engine.wren_core_engine import (
    wren_core_available,
)

_skip_when_absent = pytest.mark.skipif(
    not wren_core_available(), reason="wren-core engine not installed"
)


def requires_wren_core(func):
    """Mark a test as requiring the live wren-core engine (CI-gated)."""

    return pytest.mark.requires_wren_core(_skip_when_absent(func))
