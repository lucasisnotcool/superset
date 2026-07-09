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
"""Agent-driven prepare stage: turn dumped ``inputs/`` into fixture artifacts.

Provided scripts (prompts + infra) so a less-capable agent only supplies env/data:
``prepare_bi_docs`` (2a), ``prepare_corpus`` (2b/2c), ``prepare_targets`` (2d),
orchestrated by ``run_prepare``. See ``docs/plans/plan_eval_rig_reusable_impl.md`` §9.
"""
