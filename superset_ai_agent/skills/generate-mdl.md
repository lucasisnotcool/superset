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

# Skill: Generate / refine MDL

Goal: author MDL that the semantic engine can compile and rewrite.

- Author MDL as readable snake_case YAML; it compiles to a camelCase manifest.
- Define `relationships` (with `join_type`) so the engine can generate joins;
  do not hand-write joins into model SQL.
- Mark calculated columns with `is_calculated: true` and an `expression`.
- Prefer defined `metrics` and `cubes` over ad-hoc aggregations.
- Every physical `table_reference` must resolve to a Superset-visible object.
