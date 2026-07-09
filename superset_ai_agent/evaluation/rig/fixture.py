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
"""Fixture manifest: declares a whole experiment set without touching code.

A manifest (``fixture.yaml`` / ``.json``) is the fixture-agnostic replacement for
the hardcoded ``SCHEMAS``/fixture-dir constants in ``run_eval_v4``. It names the DB
connection, the target schemas, the onboarding mode, the context docs, and the
question corpus — everything the harness needs. Secrets never live here: a
connection is referenced by name/id, or by an *env var name* holding a URI (DP-9).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ONBOARD_MODES = ("manual", "auto", "none")
DEFAULT_GROUNDING_MODES = ("basic", "context_dump", "wren_bi", "wren_bi_context")


@dataclass(frozen=True)
class Fixture:
    """A validated experiment fixture, with paths resolved to absolute."""

    id: str
    schemas: tuple[str, ...]
    corpus_path: Path
    manifest_dir: Path
    onboard_mode: str = "none"
    database_name: str | None = None
    database_id: int | None = None
    connection_uri_env: str | None = None
    context_docs: tuple[Path, ...] = ()
    grounding_modes: tuple[str, ...] = DEFAULT_GROUNDING_MODES


class FixtureError(ValueError):
    """Raised when a manifest is missing or invalid."""


def _load_mapping(path: Path) -> dict[str, Any]:
    """Parse a YAML or JSON manifest into a dict (YAML optional)."""

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".json",):
        data = json.loads(text)
    else:
        try:
            import yaml  # noqa: PLC0415 - optional; JSON works without it
        except ImportError as ex:  # pragma: no cover - yaml ships with Superset
            raise FixtureError(
                f"{path.name} is YAML but PyYAML is not installed; "
                "use a .json manifest or install pyyaml"
            ) from ex
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise FixtureError(f"{path.name} must be a mapping at the top level")
    return data


def _resolve_context_docs(patterns: Any, base: Path) -> tuple[Path, ...]:
    """Expand context_doc globs/paths (relative to the manifest) to real files."""

    if not patterns:
        return ()
    if isinstance(patterns, str):
        patterns = [patterns]
    out: list[Path] = []
    for pat in patterns:
        matches = (
            sorted(base.glob(pat)) if any(c in pat for c in "*?[") else [base / pat]
        )
        out.extend(p for p in matches if p.is_file())
    return tuple(dict.fromkeys(out))  # dedupe, keep order


def load_fixture(path: str | Path) -> Fixture:
    """Load + validate a fixture manifest. Raises :class:`FixtureError`."""

    manifest = Path(path).resolve()
    if not manifest.is_file():
        raise FixtureError(f"fixture manifest not found: {manifest}")
    data = _load_mapping(manifest)
    base = manifest.parent

    fid = str(data.get("id") or "").strip()
    if not fid:
        raise FixtureError("manifest missing required 'id'")

    schemas_raw = data.get("schemas") or []
    if isinstance(schemas_raw, str):
        schemas_raw = [schemas_raw]
    schemas = tuple(str(s).strip() for s in schemas_raw if str(s).strip())
    if not schemas:
        raise FixtureError(f"{fid}: 'schemas' must list at least one schema")

    corpus_rel = str(data.get("corpus") or "").strip()
    if not corpus_rel:
        raise FixtureError(f"{fid}: 'corpus' (path to the question CSV) is required")
    corpus_path = (base / corpus_rel).resolve()
    if not corpus_path.is_file():
        raise FixtureError(f"{fid}: corpus not found at {corpus_path}")

    onboard_mode = str(data.get("onboard_mode") or "none").strip().lower()
    if onboard_mode not in ONBOARD_MODES:
        raise FixtureError(
            f"{fid}: onboard_mode {onboard_mode!r} not in {ONBOARD_MODES}"
        )

    database_name = data.get("database_name")
    database_id = data.get("database_id")
    connection_uri_env = data.get("connection_uri_env")
    if not any((database_name, database_id, connection_uri_env)):
        raise FixtureError(
            f"{fid}: need one of database_name / database_id / connection_uri_env"
        )

    grounding = data.get("grounding_modes") or list(DEFAULT_GROUNDING_MODES)
    if isinstance(grounding, str):
        grounding = [grounding]

    return Fixture(
        id=fid,
        schemas=schemas,
        corpus_path=corpus_path,
        manifest_dir=base,
        onboard_mode=onboard_mode,
        database_name=str(database_name) if database_name else None,
        database_id=int(database_id) if database_id is not None else None,
        connection_uri_env=(str(connection_uri_env) if connection_uri_env else None),
        context_docs=_resolve_context_docs(data.get("context_docs"), base),
        grounding_modes=tuple(str(g).strip() for g in grounding if str(g).strip()),
    )
