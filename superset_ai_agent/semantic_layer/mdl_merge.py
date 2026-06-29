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

"""Name-keyed, structure-preserving MDL manifest merge.

Shared by two authoring streams so both give the same governance guarantee:

- the **enrichment** apply path (``integrations/wren/llm_client.py``), which
  overlays an LLM-proposed manifest onto a base file; and
- the **copilot** ``patch_mdl_file`` tool (``semantic_layer/copilot/tools.py``),
  which lets the agent emit only the entities/columns it changes instead of
  re-emitting the whole file.

The merge is *additive and structure-preserving*: an entity/column the overlay
omits is kept; a colliding entity is merged by ``name`` (models merge
column-level so ``type`` and the physical mapping are never dropped); a new
named entity appends; existing order is preserved so a patch never reshuffles
the file. wren-core tolerates a dropped ``properties`` block, so structural
validation never catches the loss — this merge is what preserves it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Manifest sections merged entity-by-entity when patching an overlay into an
#: existing file (F6).
MERGE_SECTIONS: tuple[str, ...] = (
    "models",
    "relationships",
    "views",
    "metrics",
    "cubes",
)


#: Column fields an overlay may refine. Everything else on an existing column
#: (notably ``type`` and the physical mapping) is authoritative and taken from the
#: base column, so an overlay can never drop or retype a column it touches (E4).
COLUMN_SEMANTIC_FIELDS: tuple[str, ...] = ("description",)


#: Cube sub-sections whose named entries must survive a patch (H5.1).
CUBE_ENTITY_SECTIONS: tuple[str, ...] = ("measures", "dimensions", "timeDimensions")


MergeEntry = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def merge_column_preserving_structure(
    base_column: dict[str, Any], overlay_column: dict[str, Any]
) -> dict[str, Any]:
    """Overlay only semantic fields onto an existing column; keep its structure.

    ``type``, ``expression``, ``relationship``, ``isCalculated``, ``notNull`` and
    any physical mapping stay as authored on the base column. The overlay may only
    refine ``description`` and additively merge ``properties`` (e.g. synonyms).
    """

    merged = dict(base_column)
    for field in COLUMN_SEMANTIC_FIELDS:
        value = overlay_column.get(field)
        if value:
            merged[field] = value
    overlay_props = overlay_column.get("properties")
    if isinstance(overlay_props, dict) and overlay_props:
        base_props = merged.get("properties")
        base_props = base_props if isinstance(base_props, dict) else {}
        merged["properties"] = {**base_props, **overlay_props}
    return merged


def merge_model_preserving_structure(
    base_model: dict[str, Any], overlay_model: dict[str, Any]
) -> dict[str, Any]:
    """Merge an overlay onto an existing model without losing structure.

    Existing columns are preserved (only their semantics refined); a column the
    overlay omits is kept; a genuinely new column the overlay introduces is
    appended (and remains subject to physical validation downstream). The model's
    ``tableReference``/``refSql``/``primaryKey`` are never replaced by the overlay.
    This gives an overlay the same column-level structural authority that
    onboarding already has via ``_overlay_model_semantics`` (E4).
    """

    merged = dict(base_model)
    if overlay_model.get("description"):
        merged["description"] = overlay_model["description"]
    overlay_props = overlay_model.get("properties")
    if isinstance(overlay_props, dict) and overlay_props:
        base_props = merged.get("properties")
        base_props = base_props if isinstance(base_props, dict) else {}
        merged["properties"] = {**base_props, **overlay_props}

    if "columns" in base_model or "columns" in overlay_model:
        merged["columns"] = merge_columns_preserving_structure(
            base_model.get("columns", []) or [],
            overlay_model.get("columns", []) or [],
        )
    return merged


def merge_cube_preserving_structure(
    base_cube: dict[str, Any], overlay_cube: dict[str, Any]
) -> dict[str, Any]:
    """Merge an overlay cube onto an existing cube without dropping entries (H5.1).

    ``baseObject`` stays authoritative; measures/dimensions/timeDimensions the
    overlay omits are preserved, and only colliding-by-name entries are replaced.
    The agent does not author cubes today, so this defends hand-edited MDL that
    passes through the merge.
    """

    merged = dict(base_cube)
    if overlay_cube.get("description"):
        merged["description"] = overlay_cube["description"]
    if not merged.get("baseObject") and overlay_cube.get("baseObject"):
        merged["baseObject"] = overlay_cube["baseObject"]
    for section in CUBE_ENTITY_SECTIONS:
        if section in base_cube or section in overlay_cube:
            merged[section] = merge_named(
                base_cube.get(section, []) or [],
                overlay_cube.get(section, []) or [],
            )
    return merged


def merge_columns_preserving_structure(
    base_columns: list[Any], overlay_columns: list[Any]
) -> list[dict[str, Any]]:
    """Keep every base column (refining semantics); append only new overlay columns."""

    overlay_by_name = {
        col["name"]: col
        for col in overlay_columns
        if isinstance(col, dict) and isinstance(col.get("name"), str)
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for column in base_columns:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        if isinstance(name, str):
            seen.add(name)
            overlay_column = overlay_by_name.get(name)
            if isinstance(overlay_column, dict):
                column = merge_column_preserving_structure(column, overlay_column)
        result.append(dict(column))
    for column in overlay_columns:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        if isinstance(name, str) and name not in seen:
            seen.add(name)
            result.append(dict(column))
    return result


def merge_named(
    base: list[Any],
    overlay: list[Any],
    *,
    merge_entry: MergeEntry | None = None,
) -> list[dict[str, Any]]:
    """Merge two lists of ``{name: ...}`` mappings.

    When ``merge_entry`` is provided, an overlay entry colliding with a base entry
    of the same ``name`` is combined via that callable (used to preserve column
    structure on models, E4); otherwise the overlay entry replaces the base entry.
    New-named entries are appended. Order of existing entries is preserved so a
    patch never reshuffles the file.
    """

    result: list[dict[str, Any]] = [
        dict(item) for item in base if isinstance(item, dict)
    ]
    index = {
        item["name"]: pos
        for pos, item in enumerate(result)
        if isinstance(item.get("name"), str)
    }
    for item in overlay:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name in index:
            pos = index[name]
            result[pos] = (
                merge_entry(result[pos], item) if merge_entry is not None else item
            )
        else:
            result.append(item)
            if isinstance(name, str):
                index[name] = len(result) - 1
    return result


def merge_manifest_sections(
    base: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any]:
    """Overlay a proposed manifest onto a target file's content, section by section.

    The target file's other entities (and envelope keys like ``catalog``/
    ``schema``) are preserved. Models are merged **column-level** so a touched
    model keeps every existing column and its type (E4); other sections replace
    the colliding entity by name. This makes an overlay a *patch* of the owning
    file rather than a wholesale overwrite that would drop untouched models or
    columns.
    """

    merged = dict(base)
    for section in MERGE_SECTIONS:
        base_list = base.get(section)
        overlay_list = overlay.get(section)
        base_list = base_list if isinstance(base_list, list) else []
        overlay_list = overlay_list if isinstance(overlay_list, list) else []
        if base_list or overlay_list:
            # Models merge column-level (E4) and cubes entry-level (H5.1) so a
            # touched entity never loses structure; other sections replace the
            # colliding entity wholesale.
            merge_entry: MergeEntry | None = None
            if section == "models":
                merge_entry = merge_model_preserving_structure
            elif section == "cubes":
                merge_entry = merge_cube_preserving_structure
            merged[section] = merge_named(
                base_list, overlay_list, merge_entry=merge_entry
            )
    return merged
