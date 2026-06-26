<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Skill-maintenance agent reports

Each agent launched from
[`../codebase_prompt_for_agents_skill_maintenance.md`](../codebase_prompt_for_agents_skill_maintenance.md)
writes its report here, one file per skill:

- `onboarding.md`
- `generate-mdl.md`
- `enrich-context.md`

Reports follow the template defined at the end of the prompt file (summary,
extracted requirements, upstream→ours mapping, declared changes, parity gaps,
recommendations for shared files, unverified claims, verification log).

These are working artifacts of the Wren-skill tailoring pass, not runtime inputs.

The full prompt network (runtime prompts + skills + these maintenance prompts +
upstream baselines) is documented in **§AB.11 "Prompt network"** of
[`../wren_mdl_copilot.md`](../wren_mdl_copilot.md).
