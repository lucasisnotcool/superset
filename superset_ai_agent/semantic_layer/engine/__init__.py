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

"""SemanticEngine seam (wren-core parity boundary)."""

from superset_ai_agent.semantic_layer.engine.base import (
    BACKEND_TO_WREN_DIALECT,
    extract_referenced_tables,
    PlannedSql,
    resolve_dialect,
    SemanticEngine,
)
from superset_ai_agent.semantic_layer.engine.factory import create_semantic_engine
from superset_ai_agent.semantic_layer.engine.passthrough import PassthroughEngine
from superset_ai_agent.semantic_layer.engine.wren_core_engine import WrenCoreEngine

__all__ = [
    "BACKEND_TO_WREN_DIALECT",
    "PassthroughEngine",
    "PlannedSql",
    "SemanticEngine",
    "WrenCoreEngine",
    "create_semantic_engine",
    "extract_referenced_tables",
    "resolve_dialect",
]
