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

# Skill: Answering a question with the semantic layer

Goal: answer a business question with governed, trusted SQL.

1. Fetch semantic context (models, columns, relationships, examples) first.
2. When semantic-SQL mode is on, write SQL against MDL model names; the engine
   rewrites it to native SQL.
3. Validate read-only, then execute through Superset (the only executor).
4. On success, store the confirmed question/SQL pair for future recall.
5. Never reference tables or columns outside the provided semantic context.
