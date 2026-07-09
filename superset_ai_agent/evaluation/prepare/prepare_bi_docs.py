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
"""Prepare step 2a: generate the BI/onboarding doc from dumped inputs.

Reads ``inputs/`` (business context + data samples) and writes a single Markdown
context doc used by the ``context_dump`` config and by onboarding/enrichment. Core
is pure (injected chat) so it is unit-tested without a model.
"""

from __future__ import annotations

from typing import Callable

from prepare import _agent_pass as ap

BI_DOC_SYSTEM = (
    "You are writing an internal BI glossary / onboarding document for a text-to-SQL "
    "agent, from the attached business context and data samples. Produce Markdown "
    "that: maps business slang/synonyms to real columns; defines every custom metric "
    "with its exact formula; names the join paths across tables/schemas; and states "
    "any calendar, region-rollup, or status rules. Only assert what the inputs "
    "support — never invent columns, tables, or metrics. Output the Markdown only."
)


def generate_bi_doc(inputs_text: str, *, chat: Callable[[str, str], str]) -> str:
    """Produce the BI/onboarding Markdown from the concatenated inputs."""

    user = f"INPUTS:\n{inputs_text}\n\nWrite the BI onboarding document now."
    return ap.strip_code_fences(chat(BI_DOC_SYSTEM, user)).strip()
