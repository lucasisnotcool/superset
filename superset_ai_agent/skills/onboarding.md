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

# Skill: Onboarding a schema

Goal: turn a permission-filtered database schema into a documented base MDL model.

1. Resolve the semantic project for the selected `(database, catalog, schema)`.
2. Pull the permission-filtered datasets via Superset (never bypass RBAC).
3. Generate draft base MDL models (one per dataset) with descriptions.
4. Validate each draft against the live schema; hallucinated tables/columns make
   a draft non-activatable until fixed.
5. Write all output as **draft** — activation is always a human decision.
6. Never invent columns or tables absent from the permission-filtered context.
