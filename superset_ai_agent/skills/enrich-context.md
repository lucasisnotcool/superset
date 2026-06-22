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

# Skill: Enrich context from business documents

Goal: improve an existing base model using business knowledge.

1. Read the project's active MDL as the base — never invent new columns.
2. Use the supplied document text to refine descriptions, add synonyms, and
   justify metrics/relationships.
3. Return a reviewable draft; surface validation status and warnings.
4. Activation remains a human decision.
