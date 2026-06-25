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

"""MDL Copilot — agentic CRUD over a schema-scoped Wren MDL project.

This package hosts the copilot's tool registry, agentic edit loop, and the
workspace/inspector aggregation that powers the Cursor-style MDL editor. See
``superset_ai_agent/wren_mdl_copilot.md`` for the design and sequencing.
"""

from __future__ import annotations
