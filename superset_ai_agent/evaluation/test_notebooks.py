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
"""Static validation of the eval notebooks.

The experiment notebooks need a live agent to *run*, but we can still guard them
offline: every notebook must be valid ``nbformat`` JSON and every code cell must
parse as Python. This catches the most common authoring breakage (a typo in a cell)
without a server.
"""

from __future__ import annotations

import ast
import json  # noqa: TID251 - standalone eval tooling, independent of Superset
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent
NOTEBOOKS = sorted(EVAL_DIR.glob("*.ipynb"))
V2_NOTEBOOKS = {
    "06_repeated_runs.ipynb",
    "07_coverage_metric.ipynb",
    "08_copilot_path.ipynb",
    "09_distractors.ipynb",
    "10_cross_schema.ipynb",
    "11_auto_onboard.ipynb",
}


def test_notebooks_discovered():
    names = {nb.name for nb in NOTEBOOKS}
    assert V2_NOTEBOOKS <= names, f"missing v2 notebooks: {V2_NOTEBOOKS - names}"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=[nb.name for nb in NOTEBOOKS])
def test_notebook_is_valid_nbformat(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    assert nb.get("nbformat") == 4
    assert isinstance(nb.get("cells"), list)
    assert nb["cells"]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=[nb.name for nb in NOTEBOOKS])
def test_every_code_cell_parses(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    for i, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        if not source.strip():
            continue
        try:
            ast.parse(source)
        except SyntaxError as ex:  # pragma: no cover - failure path
            raise AssertionError(f"{path.name} cell {i} does not parse: {ex}") from ex


@pytest.mark.parametrize(
    "path", [EVAL_DIR / n for n in sorted(V2_NOTEBOOKS)], ids=sorted(V2_NOTEBOOKS)
)
def test_v2_notebooks_reference_the_harness_and_fixture(path):
    text = path.read_text(encoding="utf-8")
    assert "import eval_v2" in text
    assert "seagate_multi" in text
