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

"""Regression: the agent's SQL policy must import with **no** ``superset``.

The agent ships as a standalone microservice (``docker/Dockerfile.ai-agent``)
that installs only ``sqlglot`` — the ``superset`` package is absent. A previous
top-level ``from superset.sql.parse import ...`` in ``sql_policy`` crashed the
container at startup (``ModuleNotFoundError: No module named 'superset'``); the
agent's own unit tests did not catch it because they run inside the Superset
environment where ``superset`` *is* importable.

This test runs in a subprocess that hard-blocks every ``superset`` import, so it
reproduces the container environment and fails if any part of the SQL-policy
import chain reaches back into core.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Imports the full crash chain (tools.sql -> tools.sql_policy -> tools._sql_parse)
# with ``superset`` blocked, then exercises the classifier to prove it works
# without core. Prints OK only on success.
_PROGRAM = textwrap.dedent(
    """
    import sys


    class _BlockSuperset:
        \"\"\"Meta-path finder that makes ``import superset[...]`` fail.\"\"\"

        def find_spec(self, name, path, target=None):
            if name == "superset" or name.startswith("superset."):
                raise ModuleNotFoundError(f"No module named {name!r}")
            return None


    sys.meta_path.insert(0, _BlockSuperset())
    for _mod in [
        m for m in sys.modules if m == "superset" or m.startswith("superset.")
    ]:
        del sys.modules[_mod]

    from superset_ai_agent.tools.sql import validate_read_only_sql
    from superset_ai_agent.tools.sql_policy import classify_sql

    assert classify_sql("SELECT 1", engine="postgresql").kind == "read_only"
    assert classify_sql("DROP TABLE t", engine="postgresql").kind == "mutating"
    assert classify_sql("SELECT lo_export(1, '/tmp/x')", engine="postgresql").kind == (
        "mutating"
    )
    assert validate_read_only_sql("SELECT a FROM t", dialect="postgresql").is_read_only

    # Sanity: the block is actually in force.
    try:
        import superset  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("superset import was not blocked")

    print("OK")
    """
)


def test_sql_policy_imports_without_superset() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv, no untrusted input
        [sys.executable, "-c", _PROGRAM],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"SQL policy import chain reached into 'superset'.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
