# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# -----------------------------------------------------------------------
# Purge job rows whose stored result no longer validates against the schema
# (e.g. a pre-native-JSON onboarding result with content_type='application/x-yaml'
# that breaks the MDL Copilot readiness gate). Runs inside the running AI agent
# container (the no-bind stack the Windows helper uses). Wraps
# `python -m superset_ai_agent.scripts.purge_legacy_jobs`.
#
#   # Report what would be purged (no writes):
#   scripts\ai-agent-purge-legacy-jobs.ps1
#
#   # Perform the purge (backs up the SQLite DB first):
#   scripts\ai-agent-purge-legacy-jobs.ps1 -Apply
#
# The agent service must be running (start it with scripts\docker-compose-ai-up.ps1).
# -----------------------------------------------------------------------

param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFiles = @(
    "-f", "docker-compose.no-bind.yml",
    "-f", "docker-compose.ai-agent.yml"
)
$ProjectName = [regex]::Replace((Split-Path -Leaf $RepoRoot).ToLowerInvariant(), "[^a-z0-9]+", "-").Trim("-")
$env:COMPOSE_PROJECT_NAME = $ProjectName

$purgeArgs = @("python", "-m", "superset_ai_agent.scripts.purge_legacy_jobs")
if ($Apply) {
    $purgeArgs += "--apply"
}

Push-Location $RepoRoot
try {
    & docker compose @ComposeFiles exec superset-ai-agent @purgeArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
