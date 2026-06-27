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
# Diagnose / heal agent-database schema drift inside the running AI agent
# container (the no-bind stack the Windows helper uses). Wraps
# `python -m superset_ai_agent.scripts.heal_schema`.
#
#   # Report drift only (no writes):
#   scripts\ai-agent-heal-schema.ps1
#
#   # Apply the heal (migrate + create missing tables + add missing columns):
#   scripts\ai-agent-heal-schema.ps1 -Apply
#
# The agent service must be running (start it with scripts\docker-compose-ai-up.ps1).
# -----------------------------------------------------------------------

param(
    [switch]$Apply,
    [switch]$NoMigrate
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

$healArgs = @("python", "-m", "superset_ai_agent.scripts.heal_schema")
if ($Apply) {
    $healArgs += "--apply"
}
if ($NoMigrate) {
    $healArgs += "--no-migrate"
}

Push-Location $RepoRoot
try {
    & docker compose @ComposeFiles exec superset-ai-agent @healArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}
