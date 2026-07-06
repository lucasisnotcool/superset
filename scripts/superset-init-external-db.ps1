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

.PARAMETER DatabaseUri
    A full SQLAlchemy connection URI, e.g.
    postgresql+psycopg2://user:pass@host:5432/dbname?sslmode=require
    When given, it is parsed into the DATABASE_* components the config expects,
    so it takes precedence over the env files. If the password contains any of
    @ : / ? percent-encode them in the URI (e.g. '@' -> '%40'). To keep the URI
    out of your shell history, set the SUPERSET_INIT_DATABASE_URI environment
    variable instead of passing -DatabaseUri.

.EXAMPLE
    pwsh scripts/superset-init-external-db.ps1 -DatabaseUri "postgresql+psycopg2://superset:secret@db.internal:5432/superset"

.EXAMPLE
    $env:SUPERSET_INIT_DATABASE_URI = "postgresql+psycopg2://superset:secret@db.internal:5432/superset"
    pwsh scripts/superset-init-external-db.ps1

.EXAMPLE
    pwsh scripts/superset-init-external-db.ps1 -Migrate
#>
param(
    [string]$DatabaseUri,
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

# Parse a SQLAlchemy URI into the DATABASE_* components the config reconstructs.
# Splits defensively (last '@' before host, first ':' in userinfo, first '/'
# before the database) so ordinary URIs parse correctly; a query string
# (?sslmode=...) is kept on the database component so the reconstructed URI
# preserves it.
function ConvertFrom-SqlAlchemyUri {
    param([string]$Uri)

    $schemeSplit = $Uri -split '://', 2
    if ($schemeSplit.Count -ne 2) {
        throw "Invalid database URI (expected '<dialect>://user:pass@host:port/db'): $Uri"
    }
    $dialect = $schemeSplit[0]
    $rest = $schemeSplit[1]

    $atIdx = $rest.LastIndexOf('@')
    if ($atIdx -lt 1) { throw "Invalid database URI: missing '@' between credentials and host." }
    $userInfo = $rest.Substring(0, $atIdx)
    $hostPart = $rest.Substring($atIdx + 1)

    $colonIdx = $userInfo.IndexOf(':')
    if ($colonIdx -lt 1) { throw "Invalid database URI: missing ':' between user and password." }
    $user = $userInfo.Substring(0, $colonIdx)
    $pass = $userInfo.Substring($colonIdx + 1)

    $slashIdx = $hostPart.IndexOf('/')
    if ($slashIdx -lt 1) { throw "Invalid database URI: missing '/<database>'." }
    $hostPort = $hostPart.Substring(0, $slashIdx)
    $dbPart = $hostPart.Substring($slashIdx + 1)
    if ([string]::IsNullOrEmpty($dbPart)) { throw "Invalid database URI: empty database name." }

    $hpColon = $hostPort.LastIndexOf(':')
    if ($hpColon -ge 1) {
        $dbHost = $hostPort.Substring(0, $hpColon)
        $dbPort = $hostPort.Substring($hpColon + 1)
    }
    else {
        $dbHost = $hostPort
        $dbPort = "5432"
    }

    return @{
        DATABASE_DIALECT  = $dialect
        DATABASE_USER     = $user
        DATABASE_PASSWORD = $pass
        DATABASE_HOST     = $dbHost
        DATABASE_PORT     = $dbPort
        DATABASE_DB       = $dbPart
    }
}

# Resolve the connection: an explicit URI (-DatabaseUri, or the
# SUPERSET_INIT_DATABASE_URI env var) wins; otherwise fall back to the
# DATABASE_* components read from the env files.
$envVars = @{}
$effectiveUri = if ($DatabaseUri) {
    $DatabaseUri
}
elseif ($env:SUPERSET_INIT_DATABASE_URI) {
    $env:SUPERSET_INIT_DATABASE_URI
}
else {
    $null
}

if ($effectiveUri) {
    $envVars = ConvertFrom-SqlAlchemyUri -Uri $effectiveUri
    $source = if ($DatabaseUri) { "-DatabaseUri parameter" } else { "SUPERSET_INIT_DATABASE_URI env var" }
    Write-Host "Using database URI from $source"
}
else {
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
    throw "Missing DATABASE_* variables: $($missing -join ', '). Pass -DatabaseUri '<sqlalchemy-uri>' (or set SUPERSET_INIT_DATABASE_URI), or provide the DATABASE_* values via -EnvFile, and retry."
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
