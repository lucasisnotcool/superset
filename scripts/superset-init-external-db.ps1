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

<#
.SYNOPSIS
    Run `superset init` against the deployment's external PostgreSQL from your
    local box, to (re)create the role definitions — including the custom
    "Builder" role — without touching CI or the running cluster.

.DESCRIPTION
    Reads the DATABASE_* connection settings from the local env files
    (docker/.env then docker/.env-local, later wins — the same files docker
    compose reads), prints the resolved target for confirmation, then runs a
    one-off container from the local `superset` image. That container loads the
    BuilderSecurityManager config (via the baked docker/pythonpath_dev
    superset_config) and points SQLALCHEMY_DATABASE_URI at the external database
    through -e overrides, so it connects to prod regardless of the image's baked
    defaults.

    Because the metadata database is persistent, the roles it writes stay there.

    SCOPE: this creates/refreshes the *role* in the database. It does NOT change
    what the running cluster loads at runtime — auth type, LDAP, and
    registration-role settings still live in the deployment's own
    superset_config (/app/pythonpath/superset_config.py) and must be set there.

.PARAMETER EnvFile
    Ordered list of env files to read DATABASE_* from (later overrides earlier).
    Defaults to docker/.env then docker/.env-local.

.PARAMETER Migrate
    Also run `superset db upgrade` before `superset init`. Off by default —
    schema migrations are normally owned by the deploy pipeline. Only use this
    if you intentionally want to apply THIS checkout's migrations to the
    external DB, and only when this checkout is on the deployed commit.

.PARAMETER Force
    Skip the interactive confirmation prompt (for non-interactive use).

.EXAMPLE
    pwsh scripts/superset-init-external-db.ps1

.EXAMPLE
    pwsh scripts/superset-init-external-db.ps1 -Migrate
#>
param(
    [string[]]$EnvFile = @("docker/.env", "docker/.env-local"),
    [switch]$Migrate,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFiles = @(
    "-f", "docker-compose.no-bind.yml",
    "-f", "docker-compose.ai-agent.yml"
)

# Minimal .env parser: KEY=VALUE per line, ignores blanks and # comments,
# strips one layer of surrounding single or double quotes.
function Import-DotEnv {
    param([string]$Path)

    $result = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $result
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) {
            continue
        }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $idx).Trim()
        $val = $trimmed.Substring($idx + 1).Trim()
        if ($val.Length -ge 2) {
            $first = $val[0]
            $last = $val[$val.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        $result[$key] = $val
    }
    return $result
}

# Merge env files in order; later files override earlier ones.
$envVars = @{}
foreach ($rel in $EnvFile) {
    $full = Join-Path $RepoRoot $rel
    if (Test-Path -LiteralPath $full) {
        $parsed = Import-DotEnv -Path $full
        foreach ($k in $parsed.Keys) {
            $envVars[$k] = $parsed[$k]
        }
        Write-Host "Loaded env from $rel"
    }
    else {
        Write-Warning "Env file not found (skipped): $rel"
    }
}

$required = @(
    "DATABASE_DIALECT",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_DB"
)
$missing = $required | Where-Object { [string]::IsNullOrEmpty($envVars[$_]) }
if ($missing) {
    throw "Missing DATABASE_* variables: $($missing -join ', '). Set the external Postgres connection in docker/.env-local (or pass -EnvFile) and retry."
}

# Guard against silently targeting the local compose DB. With --no-deps there is
# no local `db` container, so pointing at it would just fail to connect.
if ($envVars["DATABASE_HOST"] -in @("db", "localhost", "127.0.0.1")) {
    Write-Warning "DATABASE_HOST is '$($envVars["DATABASE_HOST"])' — that looks like a LOCAL database, not the deployment's external one. Make sure docker/.env-local points at the external Postgres."
}

Write-Host ""
Write-Host "superset init will run against:" -ForegroundColor Cyan
Write-Host ("  host : {0}:{1}" -f $envVars["DATABASE_HOST"], $envVars["DATABASE_PORT"])
Write-Host ("  db   : {0}" -f $envVars["DATABASE_DB"])
Write-Host ("  user : {0}" -f $envVars["DATABASE_USER"])
Write-Host ("  mode : {0}" -f $(if ($Migrate) { "db upgrade + init" } else { "init only" }))
Write-Host ""

if (-not $Force) {
    $answer = Read-Host "This WRITES roles/permissions to the database above. Continue? (yes/no)"
    if ($answer -ne "yes") {
        Write-Host "Aborted."
        exit 1
    }
}

# Pass the resolved connection as -e overrides so the one-off container connects
# to the external DB regardless of the image's baked docker/.env defaults.
$eArgs = @()
foreach ($k in $required) {
    $eArgs += @("-e", "$k=$($envVars[$k])")
}

if ($Migrate) {
    $cmd = @("sh", "-c", "superset db upgrade && superset init")
}
else {
    $cmd = @("superset", "init")
}

Push-Location $RepoRoot
try {
    & docker compose @ComposeFiles run --rm --no-deps @eArgs superset @cmd
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Error "superset init failed (exit $code)."
    exit $code
}

Write-Host ""
Write-Host "Done. Verify in your SQL client:" -ForegroundColor Green
Write-Host "  SELECT id, name FROM ab_role WHERE name = 'Builder';"
