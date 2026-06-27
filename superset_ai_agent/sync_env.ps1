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

<#
.SYNOPSIS
  Sync .env.example (the structural source of truth) into the live .env.

.DESCRIPTION
  Policy:
    1. A variable present in .env but NOT in .env.example is removed.
    2. A variable present in .env.example but NOT in .env is copied in,
       using the example's value.
    3. A variable present in BOTH keeps its existing .env value (no change);
       the script echoes the variable name, line numbers, and both values.

  The synced .env mirrors the example's ordering, comments, and blank lines, so
  it matches .env.example line-for-line except for values that already existed
  in .env (e.g. real secrets), which are preserved.

  A timestamped backup (.env.bak) is written before overwriting unless -NoBackup
  is given. Use -DryRun to preview the report without writing anything.

.PARAMETER ExamplePath
  Path to the example file. Default: .env.example next to this script.

.PARAMETER EnvPath
  Path to the live env file to update. Default: .env next to this script.

.PARAMETER DryRun
  Report what would change but do not write the .env file or a backup.

.PARAMETER NoBackup
  Skip writing the .env.bak backup before overwriting.

.PARAMETER Mask
  Mask values in the console report (show only the last 4 characters). Useful
  to avoid printing secrets to the terminal or CI logs.

.EXAMPLE
  ./sync_env.ps1
.EXAMPLE
  ./sync_env.ps1 -DryRun
.EXAMPLE
  ./sync_env.ps1 -ExamplePath .env.example -EnvPath .env -Mask
#>
[CmdletBinding()]
param(
    [string]$ExamplePath = (Join-Path $PSScriptRoot '.env.example'),
    [string]$EnvPath     = (Join-Path $PSScriptRoot '.env'),
    [switch]$DryRun,
    [switch]$NoBackup,
    [switch]$Mask
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ExamplePath)) {
    throw "Example file not found: $ExamplePath"
}

# Matches a `NAME=value` assignment; allows leading spaces and an optional `export`.
# Value is captured greedily so embedded '=' (URLs, base64 keys) stays intact.
$assignRegex = '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$'

function Format-Val {
    param([string]$Value)
    if (-not $Mask) { return $Value }
    if ($Value.Length -le 4) { return ('*' * $Value.Length) }
    return ('*' * ($Value.Length - 4)) + $Value.Substring($Value.Length - 4)
}

function Read-EnvVars {
    param([string]$Path)
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    $lineNo = 0
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $lineNo++
        $m = [regex]::Match($line, $assignRegex)
        if (-not $m.Success) { continue }
        $name = $m.Groups[1].Value
        $value = $m.Groups[2].Value
        if ($map.Contains($name)) {
            # Last assignment wins (dotenv semantics); keep first line for reporting.
            $map[$name].Value = $value
        } else {
            $map[$name] = [pscustomobject]@{ Value = $value; Line = $lineNo }
        }
    }
    return $map
}

$envVars = Read-EnvVars -Path $EnvPath
$exampleLines = [System.IO.File]::ReadAllLines($ExamplePath)

$output       = New-Object System.Collections.Generic.List[string]
$exampleNames = New-Object System.Collections.Generic.HashSet[string]
$unchanged    = New-Object System.Collections.Generic.List[object]
$added        = New-Object System.Collections.Generic.List[object]

$exLineNo = 0
foreach ($line in $exampleLines) {
    $exLineNo++
    $m = [regex]::Match($line, $assignRegex)
    if (-not $m.Success) {
        # Comment or blank line: copy the example's structure verbatim.
        $output.Add($line)
        continue
    }
    $name = $m.Groups[1].Value
    $exValue = $m.Groups[2].Value
    [void]$exampleNames.Add($name)

    if ($envVars.Contains($name)) {
        # Policy 3: in both -> keep the live .env value, report the pair.
        $envValue = $envVars[$name].Value
        $output.Add("$name=$envValue")
        $unchanged.Add([pscustomobject]@{
            Name         = $name
            EnvLine      = $envVars[$name].Line
            ExampleLine  = $exLineNo
            EnvValue     = $envValue
            ExampleValue = $exValue
        })
    } else {
        # Policy 2: only in example -> copy it in with the example's value.
        $output.Add($line)
        $added.Add([pscustomobject]@{ Name = $name; ExampleLine = $exLineNo; Value = $exValue })
    }
}

# Policy 1: variables in .env but not in example are dropped (never emitted).
$removed = New-Object System.Collections.Generic.List[object]
foreach ($name in $envVars.Keys) {
    if (-not $exampleNames.Contains($name)) {
        $removed.Add([pscustomobject]@{ Name = $name; EnvLine = $envVars[$name].Line; Value = $envVars[$name].Value })
    }
}

# --- Report ---------------------------------------------------------------
Write-Host ""
Write-Host "=== In BOTH (unchanged; kept .env value) ===" -ForegroundColor Cyan
if ($unchanged.Count -eq 0) {
    Write-Host "  (none)"
} else {
    foreach ($v in $unchanged) {
        Write-Host ("  {0}  (.env line {1}, example line {2})" -f $v.Name, $v.EnvLine, $v.ExampleLine)
        Write-Host ("      .env    = {0}" -f (Format-Val $v.EnvValue))
        Write-Host ("      example = {0}" -f (Format-Val $v.ExampleValue))
    }
}

Write-Host ""
Write-Host "=== Copied IN from example (missing in .env) ===" -ForegroundColor Green
if ($added.Count -eq 0) { Write-Host "  (none)" }
else { foreach ($v in $added) { Write-Host ("  + {0}  (example line {1}) = {2}" -f $v.Name, $v.ExampleLine, (Format-Val $v.Value)) } }

Write-Host ""
Write-Host "=== Removed from .env (not in example) ===" -ForegroundColor Yellow
if ($removed.Count -eq 0) { Write-Host "  (none)" }
else { foreach ($v in $removed) { Write-Host ("  - {0}  (was .env line {1}) = {2}" -f $v.Name, $v.EnvLine, (Format-Val $v.Value)) } }

# --- Write the synced file (LF endings, UTF-8 without BOM) ----------------
$text = ($output -join "`n") + "`n"

if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN: no files written." -ForegroundColor Magenta
    Write-Host ("Would sync {0} -> {1}  (unchanged: {2}, added: {3}, removed: {4})" -f `
        $ExamplePath, $EnvPath, $unchanged.Count, $added.Count, $removed.Count)
    return
}

if (-not $NoBackup -and (Test-Path -LiteralPath $EnvPath)) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$EnvPath.$stamp.bak"
    Copy-Item -LiteralPath $EnvPath -Destination $backup -Force
    Write-Host ""
    Write-Host "Backup written: $backup" -ForegroundColor DarkGray
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvPath, $text, $utf8NoBom)

Write-Host ""
Write-Host ("Synced {0} -> {1}" -f $ExamplePath, $EnvPath) -ForegroundColor Cyan
Write-Host ("  unchanged: {0}   added: {1}   removed: {2}" -f $unchanged.Count, $added.Count, $removed.Count)
