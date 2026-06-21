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

param(
    [string]$BaseUrl,
    [int]$Tail = 160,
    [string]$OutputPath,
    [switch]$FailOnFindings,
    [switch]$SkipLogs
)

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFiles = @(
    "-f", "docker-compose.no-bind.yml",
    "-f", "docker-compose.ai-agent.yml"
)
$ProjectName = [regex]::Replace((Split-Path -Leaf $RepoRoot).ToLowerInvariant(), "[^a-z0-9]+", "-").Trim("-")
$env:COMPOSE_PROJECT_NAME = $ProjectName

$Findings = New-Object System.Collections.Generic.List[object]

function Add-Finding {
    param(
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level,
        [string]$Message
    )

    $Findings.Add([pscustomobject]@{
        Level = $Level
        Message = $Message
    }) | Out-Null
}

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host ("=" * 78)
    Write-Host $Title
    Write-Host ("=" * 78)
}

function Write-Subsection {
    param([string]$Title)

    Write-Host ""
    Write-Host "--- $Title ---"
}

function Invoke-Docker {
    param([string[]]$Arguments)

    $hasNativePreference = Test-Path Variable:\PSNativeCommandUseErrorActionPreference
    if ($hasNativePreference) {
        $previousNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } catch {
        $output = $_ | Out-String
        $exitCode = 1
    } finally {
        if ($hasNativePreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativePreference
        }
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = (($output | Out-String).TrimEnd())
    }
}

function Invoke-Compose {
    param([string[]]$Arguments)

    return Invoke-Docker -Arguments (@("compose") + $ComposeFiles + $Arguments)
}

function Write-CommandResult {
    param(
        [string]$Label,
        [object]$Result,
        [switch]$AllowFailure
    )

    Write-Subsection $Label
    Write-Host "ExitCode: $($Result.ExitCode)"
    if ($Result.Output) {
        Write-Host $Result.Output
    }
    if ($Result.ExitCode -ne 0 -and -not $AllowFailure) {
        Add-Finding -Level "ERROR" -Message "$Label failed with exit code $($Result.ExitCode)."
    }
}

function Get-FirstOutputLine {
    param([string]$Output)

    if (-not $Output) {
        return $null
    }

    return ($Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
}

function Get-ServiceContainerId {
    param([string]$Service)

    $result = Invoke-Compose -Arguments @("ps", "-q", $Service)
    if ($result.ExitCode -ne 0) {
        return $null
    }

    return Get-FirstOutputLine -Output $result.Output
}

function Invoke-ContainerCommand {
    param(
        [string]$Service,
        [string]$Command
    )

    $containerId = Get-ServiceContainerId -Service $Service
    if (-not $containerId) {
        return [pscustomobject]@{
            ExitCode = 1
            Output = "Container for service '$Service' was not found."
        }
    }

    return Invoke-Docker -Arguments @("exec", $containerId, "sh", "-lc", $Command)
}

function Get-PublishedNginxPort {
    $result = Invoke-Compose -Arguments @("port", "nginx", "80")
    if ($result.ExitCode -ne 0 -or -not $result.Output) {
        return $null
    }

    $line = Get-FirstOutputLine -Output $result.Output
    if ($line -match ":(\d+)$") {
        return [int]$Matches[1]
    }

    return $null
}

function ConvertTo-AbsoluteUrl {
    param([string]$PathOrUrl)

    if ($PathOrUrl -match "^https?://") {
        return $PathOrUrl
    }

    if ($PathOrUrl.StartsWith("/")) {
        return "$($script:ResolvedBaseUrl.TrimEnd('/'))$PathOrUrl"
    }

    return "$($script:ResolvedBaseUrl.TrimEnd('/'))/$PathOrUrl"
}

function Invoke-HttpProbe {
    param(
        [string]$Url,
        [switch]$ReturnContent
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest `
            -Uri $Url `
            -UseBasicParsing `
            -TimeoutSec 20 `
            -Headers @{ "Cache-Control" = "no-cache" }
        $stopwatch.Stop()

        $content = ""
        if ($ReturnContent) {
            $content = [string]$response.Content
        }

        return [pscustomobject]@{
            Url = $Url
            Ok = $true
            StatusCode = [int]$response.StatusCode
            ContentType = [string]$response.Headers["Content-Type"]
            Bytes = ([string]$response.Content).Length
            Milliseconds = [int]$stopwatch.ElapsedMilliseconds
            Error = ""
            Content = $content
        }
    } catch {
        $stopwatch.Stop()
        $statusCode = $null
        $contentType = ""
        if ($_.Exception.Response) {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode
                $contentType = [string]$_.Exception.Response.ContentType
            } catch {
                $statusCode = $null
            }
        }

        return [pscustomobject]@{
            Url = $Url
            Ok = $false
            StatusCode = $statusCode
            ContentType = $contentType
            Bytes = 0
            Milliseconds = [int]$stopwatch.ElapsedMilliseconds
            Error = $_.Exception.Message
            Content = ""
        }
    }
}

function Write-HttpProbe {
    param([object]$Probe)

    $status = if ($Probe.Ok) { "OK" } else { "FAIL" }
    $code = if ($null -ne $Probe.StatusCode) { $Probe.StatusCode } else { "-" }
    Write-Host ("{0,-5} {1,-4} {2,6} bytes {3,6} ms {4}" -f $status, $code, $Probe.Bytes, $Probe.Milliseconds, $Probe.Url)
    if ($Probe.ContentType) {
        Write-Host "      Content-Type: $($Probe.ContentType)"
    }
    if ($Probe.Error) {
        Write-Host "      Error: $($Probe.Error)"
    }
}

function Get-RegexMatches {
    param(
        [string]$InputText,
        [string]$Pattern
    )

    return [regex]::Matches($InputText, $Pattern, "IgnoreCase, Singleline") |
        ForEach-Object { $_.Groups[1].Value } |
        Sort-Object -Unique
}

function Test-ServiceMount {
    param(
        [string]$Service,
        [string]$Destination
    )

    $containerId = Get-ServiceContainerId -Service $Service
    if (-not $containerId) {
        Write-Host "$Service`: container not found"
        Add-Finding -Level "ERROR" -Message "$Service container was not found."
        return $false
    }

    $inspect = Invoke-Docker -Arguments @("inspect", $containerId)
    if ($inspect.ExitCode -ne 0) {
        Write-Host "$Service`: docker inspect failed"
        Add-Finding -Level "ERROR" -Message "docker inspect failed for $Service."
        return $false
    }

    try {
        $container = ($inspect.Output | ConvertFrom-Json)[0]
        $mount = $container.Mounts | Where-Object { $_.Destination -eq $Destination } | Select-Object -First 1
        if ($mount) {
            $source = if ($mount.Name) { $mount.Name } else { $mount.Source }
            Write-Host "$Service`: $($mount.Type) $source -> $Destination"
            return $true
        }

        Write-Host "$Service`: MISSING mount -> $Destination"
        Add-Finding -Level "ERROR" -Message "$Service is missing mount $Destination."
        return $false
    } catch {
        Write-Host "$Service`: failed to parse docker inspect output: $($_.Exception.Message)"
        Add-Finding -Level "ERROR" -Message "Failed to parse docker inspect output for $Service."
        return $false
    }
}

function Write-ServiceHealth {
    param([string[]]$Services)

    foreach ($service in $Services) {
        $containerId = Get-ServiceContainerId -Service $service
        if (-not $containerId) {
            Write-Host ("{0,-24} not found" -f $service)
            Add-Finding -Level "WARN" -Message "$service container was not found."
            continue
        }

        $inspect = Invoke-Docker -Arguments @(
            "inspect",
            "--format",
            "{{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} started={{.State.StartedAt}}",
            $containerId
        )
        if ($inspect.Output) {
            Write-Host $inspect.Output
        } else {
            Write-Host ("{0,-24} inspect failed" -f $service)
        }
    }
}

function ConvertFrom-ManifestContent {
    param([string]$Content)

    if (-not $Content) {
        return $null
    }

    try {
        return $Content | ConvertFrom-Json
    } catch {
        Add-Finding -Level "ERROR" -Message "Host manifest JSON could not be parsed: $($_.Exception.Message)"
        return $null
    }
}

function Get-ManifestEntrypointScripts {
    param(
        [object]$Manifest,
        [string[]]$Entrypoints
    )

    $scripts = New-Object System.Collections.Generic.List[string]
    if (-not $Manifest -or -not $Manifest.entrypoints) {
        return $scripts
    }

    foreach ($entrypoint in $Entrypoints) {
        $entry = $Manifest.entrypoints.$entrypoint
        if (-not $entry -or -not $entry.js) {
            continue
        }
        foreach ($script in $entry.js) {
            $scripts.Add([string]$script) | Out-Null
        }
    }

    return $scripts | Sort-Object -Unique
}

function Write-ManifestSummary {
    param([object]$Manifest)

    if (-not $Manifest -or -not $Manifest.entrypoints) {
        Write-Host "Manifest has no entrypoints."
        return
    }

    $entrypointNames = $Manifest.entrypoints.PSObject.Properties.Name
    Write-Host "Entrypoints: $($entrypointNames -join ', ')"
    foreach ($entrypoint in @("preamble", "spa", "menu", "embedded")) {
        $entry = $Manifest.entrypoints.$entrypoint
        if (-not $entry) {
            Write-Host "$entrypoint`: missing"
            continue
        }
        $jsCount = @($entry.js).Count
        $cssCount = @($entry.css).Count
        Write-Host "$entrypoint`: js=$jsCount css=$cssCount"
        foreach ($script in @($entry.js) | Select-Object -First 8) {
            Write-Host "  $script"
        }
    }
}

function Analyze-Html {
    param([string]$Html)

    $scriptSources = @(Get-RegexMatches -InputText $Html -Pattern '<script[^>]+src="([^"]+)"')
    $entryScripts = @(
        $scriptSources |
            Where-Object { $_ -match "/static/assets/.*\.entry\.js(\?.*)?$" } |
            Sort-Object -Unique
    )
    $staticAssets = @(
        Get-RegexMatches `
            -InputText $Html `
            -Pattern '(?:src|href)="([^"]*/static/assets/[^"]+)"'
    )
    $bundleComments = @(
        Get-RegexMatches `
            -InputText $Html `
            -Pattern '<!--\s*Bundle\s+js\s+([^<]+?)\s*-->'
    )
    $title = ""
    $titleMatch = [regex]::Match($Html, '<title>\s*(.*?)\s*</title>', "IgnoreCase, Singleline")
    if ($titleMatch.Success) {
        $title = ($titleMatch.Groups[1].Value -replace "\s+", " ").Trim()
    }

    Write-Host "HTML bytes: $($Html.Length)"
    Write-Host "Title: $title"
    Write-Host "Has #app: $($Html.Contains('id="app"') -or $Html.Contains('id=''app'''))"
    Write-Host "Has data-bootstrap: $($Html.Contains('data-bootstrap='))"
    Write-Host "Script tags: $($scriptSources.Count)"
    Write-Host "Entry script tags: $($entryScripts.Count)"
    Write-Host "Static asset refs: $($staticAssets.Count)"

    if ($bundleComments.Count -gt 0) {
        Write-Host "Bundle comments:"
        foreach ($comment in $bundleComments | Select-Object -First 20) {
            Write-Host "  $comment"
        }
    }

    if ($scriptSources.Count -gt 0) {
        Write-Host "First script src values:"
        foreach ($script in $scriptSources | Select-Object -First 20) {
            Write-Host "  $script"
        }
    }

    if ($entryScripts.Count -eq 0) {
        Add-Finding -Level "ERROR" -Message "HTML contains no /static/assets/*.entry.js script tags. React will stay on the spinner."
    }

    return [pscustomobject]@{
        ScriptSources = $scriptSources
        EntryScripts = $entryScripts
        StaticAssets = $staticAssets
    }
}

function Write-AssetProbes {
    param([string[]]$Paths)

    $uniquePaths = @($Paths | Where-Object { $_ } | Sort-Object -Unique | Select-Object -First 16)
    if ($uniquePaths.Count -eq 0) {
        Write-Host "No asset paths to probe."
        return
    }

    foreach ($path in $uniquePaths) {
        $probe = Invoke-HttpProbe -Url (ConvertTo-AbsoluteUrl -PathOrUrl $path)
        Write-HttpProbe -Probe $probe
        if (-not $probe.Ok -or $probe.StatusCode -ne 200) {
            Add-Finding -Level "ERROR" -Message "Asset probe failed for $path."
        }
    }
}

function Write-ContainerManifestReport {
    param([string]$Service)

    $manifestCommand = @'
p=/app/superset/static/assets/manifest.json
echo "path=$p"
if [ ! -s "$p" ]; then
  echo "MISSING_OR_EMPTY"
  exit 0
fi
ls -l "$p"
(sha256sum "$p" 2>/dev/null || shasum -a 256 "$p" 2>/dev/null || true)
python - <<'PY'
import json
p = "/app/superset/static/assets/manifest.json"
with open(p) as handle:
    data = json.load(handle)
entrypoints = data.get("entrypoints", {})
print("entrypoints=" + ",".join(sorted(entrypoints)))
for key in ("preamble", "spa", "menu", "embedded"):
    entry = entrypoints.get(key, {})
    scripts = entry.get("js", [])
    styles = entry.get("css", [])
    print(f"{key}: js={len(scripts)} css={len(styles)}")
    for script in scripts[:8]:
        print("  " + script)
PY
'@

    $result = Invoke-ContainerCommand -Service $Service -Command $manifestCommand
    Write-CommandResult -Label "$Service manifest file" -Result $result -AllowFailure
    if ($result.Output -match "MISSING_OR_EMPTY") {
        Add-Finding -Level "ERROR" -Message "$Service has no readable webpack manifest at /app/superset/static/assets/manifest.json."
    }
}

function Main {
    Push-Location $RepoRoot
    try {
        Write-Section "Diagnostics Context"
        Write-Host "Timestamp: $(Get-Date -Format o)"
        Write-Host "Repo root: $RepoRoot"
        Write-Host "Project name: $ProjectName"
        Write-Host "Compose files: $($ComposeFiles -join ' ')"
        Write-Host "PowerShell: $($PSVersionTable.PSVersion)"

        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Add-Finding -Level "ERROR" -Message "docker was not found on PATH."
            Write-Host "docker was not found on PATH."
            return
        }

        Write-CommandResult -Label "docker version" -Result (Invoke-Docker -Arguments @("version")) -AllowFailure
        Write-CommandResult -Label "docker compose version" -Result (Invoke-Docker -Arguments @("compose", "version")) -AllowFailure
        Write-CommandResult -Label "git status" -Result ([pscustomobject]@{
            ExitCode = 0
            Output = ((& git status --short 2>&1) | Out-String).TrimEnd()
        }) -AllowFailure

        Write-Section "Compose State"
        Write-CommandResult -Label "docker compose config --services" -Result (Invoke-Compose -Arguments @("config", "--services"))
        Write-CommandResult -Label "docker compose config --volumes" -Result (Invoke-Compose -Arguments @("config", "--volumes")) -AllowFailure
        Write-CommandResult -Label "docker compose ps" -Result (Invoke-Compose -Arguments @("ps"))

        if (-not $BaseUrl) {
            $port = Get-PublishedNginxPort
            if ($port) {
                $BaseUrl = "http://localhost:$port"
            } else {
                $BaseUrl = "http://localhost:8090"
                Add-Finding -Level "WARN" -Message "Could not detect the nginx published port; defaulting to $BaseUrl."
            }
        }
        $script:ResolvedBaseUrl = $BaseUrl.TrimEnd("/")
        Write-Host "Resolved base URL: $script:ResolvedBaseUrl"

        Write-Section "Container Health"
        Write-ServiceHealth -Services @(
            "nginx",
            "superset",
            "superset-node",
            "superset-ai-agent",
            "superset-websocket",
            "superset-worker",
            "superset-worker-beat",
            "superset-init",
            "db",
            "redis"
        )

        Write-Section "Required Mounts"
        $assetsPath = "/app/superset/static/assets"
        [void](Test-ServiceMount -Service "superset" -Destination $assetsPath)
        [void](Test-ServiceMount -Service "superset-node" -Destination $assetsPath)
        [void](Test-ServiceMount -Service "superset-init" -Destination $assetsPath)

        Write-Section "Nginx Runtime Config"
        $nginxConfigCommand = @'
echo "--- /etc/nginx/conf.d/superset.conf locations/upstreams ---"
grep -nE "upstream|server |location|proxy_pass" /etc/nginx/conf.d/superset.conf || true
echo "--- static locations ---"
grep -nE "location .*/static" /etc/nginx/conf.d/superset.conf || true
'@
        $nginxConfig = Invoke-ContainerCommand -Service "nginx" -Command $nginxConfigCommand
        Write-CommandResult -Label "nginx rendered config" -Result $nginxConfig -AllowFailure
        if ($nginxConfig.Output -notmatch "location /static/assets") {
            Add-Finding -Level "ERROR" -Message "nginx is not configured with location /static/assets."
        }

        Write-Section "Host HTTP Probes"
        $hostProbePaths = @(
            "/health",
            "/sqllab/",
            "/static/assets/manifest.json",
            "/static/service-worker.js",
            "/static/appbuilder/css/flags/flags16.css",
            "/ai-agent/health"
        )
        foreach ($path in $hostProbePaths) {
            $probe = Invoke-HttpProbe -Url (ConvertTo-AbsoluteUrl -PathOrUrl $path)
            Write-HttpProbe -Probe $probe
            if ($path -ne "/ai-agent/health" -and (-not $probe.Ok -or $probe.StatusCode -ne 200)) {
                Add-Finding -Level "ERROR" -Message "Host HTTP probe failed for $path."
            }
        }

        Write-Section "HTML Analysis"
        $htmlProbe = Invoke-HttpProbe -Url (ConvertTo-AbsoluteUrl -PathOrUrl "/sqllab/") -ReturnContent
        Write-HttpProbe -Probe $htmlProbe
        if (-not $htmlProbe.Ok -or $htmlProbe.StatusCode -ne 200) {
            Add-Finding -Level "ERROR" -Message "Could not fetch /sqllab/ HTML."
            $htmlAnalysis = [pscustomobject]@{
                ScriptSources = @()
                EntryScripts = @()
                StaticAssets = @()
            }
        } else {
            $htmlAnalysis = Analyze-Html -Html $htmlProbe.Content
        }

        Write-Section "Host Manifest Analysis"
        $manifestProbe = Invoke-HttpProbe -Url (ConvertTo-AbsoluteUrl -PathOrUrl "/static/assets/manifest.json") -ReturnContent
        Write-HttpProbe -Probe $manifestProbe
        $manifest = $null
        $manifestScripts = @()
        if ($manifestProbe.Ok -and $manifestProbe.StatusCode -eq 200) {
            $manifest = ConvertFrom-ManifestContent -Content $manifestProbe.Content
            Write-ManifestSummary -Manifest $manifest
            $manifestScripts = @(
                Get-ManifestEntrypointScripts `
                    -Manifest $manifest `
                    -Entrypoints @("preamble", "spa")
            )
        } else {
            Add-Finding -Level "ERROR" -Message "Host manifest probe failed."
        }

        Write-Section "Entrypoint Asset Probes"
        $pathsToProbe = @($htmlAnalysis.EntryScripts)
        if ($pathsToProbe.Count -eq 0) {
            Write-Host "HTML had no entry scripts; probing preamble/spa scripts from manifest instead."
            $pathsToProbe = @($manifestScripts)
        }
        Write-AssetProbes -Paths $pathsToProbe

        Write-Section "Container Manifest Analysis"
        Write-ContainerManifestReport -Service "superset"
        Write-ContainerManifestReport -Service "superset-node"

        Write-Section "Superset Runtime Env"
        $envCommand = 'printenv | grep -E "^(FLASK_DEBUG|SUPERSET_ENV|SUPERSET_APP_ROOT|SUPERSET_PORT|PYTHONPATH)=" || true'
        Write-CommandResult -Label "superset selected env" -Result (Invoke-ContainerCommand -Service "superset" -Command $envCommand) -AllowFailure

        Write-Section "Internal Network Probes From Nginx"
        $internalProbeCommand = @'
for url in \
  http://superset:8088/health \
  http://superset:8088/sqllab/ \
  http://superset-node:9000/static/assets/manifest.json \
  http://superset-ai-agent:5050/health \
  http://superset-websocket:8080/health
do
  echo "--- $url"
  curl -sS -o /dev/null -w "status=%{http_code} content_type=%{content_type} bytes=%{size_download} time=%{time_total}\n" "$url" || true
done
echo "--- direct superset /sqllab/ entry scripts"
curl -sS http://superset:8088/sqllab/ | grep -oE '/static/assets/[^"]+\.entry\.js' | head -20 || true
'@
        Write-CommandResult -Label "nginx internal curl probes" -Result (Invoke-ContainerCommand -Service "nginx" -Command $internalProbeCommand) -AllowFailure

        Write-Section "Recent Nginx Logs"
        $nginxLogCommand = @"
echo "--- access.log filtered ---"
tail -n $Tail /var/log/nginx/access.log 2>/dev/null | grep -E "sqllab|static/assets|entry\.js|chunk\.js|manifest|appbuilder|api|ws|well-known" || true
echo "--- error.log ---"
tail -n $Tail /var/log/nginx/error.log 2>/dev/null || true
"@
        Write-CommandResult -Label "nginx access/error logs" -Result (Invoke-ContainerCommand -Service "nginx" -Command $nginxLogCommand) -AllowFailure

        if (-not $SkipLogs) {
            Write-Section "Recent Compose Logs"
            Write-CommandResult `
                -Label "docker compose logs selected services" `
                -Result (Invoke-Compose -Arguments @(
                    "logs",
                    "--tail",
                    "$Tail",
                    "nginx",
                    "superset",
                    "superset-node",
                    "superset-ai-agent"
                )) `
                -AllowFailure
        }

        Write-Section "Findings"
        if ($Findings.Count -eq 0) {
            Write-Host "No findings were raised by this script."
        } else {
            foreach ($finding in $Findings) {
                Write-Host ("[{0}] {1}" -f $finding.Level, $finding.Message)
            }
        }

        $errors = @($Findings | Where-Object { $_.Level -eq "ERROR" })
        if ($errors.Count -gt 0) {
            Write-Host ""
            Write-Host "Diagnostics completed with $($errors.Count) error-level finding(s)."
            if ($FailOnFindings) {
                $script:ExitCode = 2
            } else {
                $script:ExitCode = 0
            }
        } else {
            Write-Host ""
            Write-Host "Diagnostics completed without error-level findings."
            $script:ExitCode = 0
        }
    } finally {
        Pop-Location
    }
}

if ($OutputPath) {
    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory -and -not (Test-Path $outputDirectory)) {
        New-Item -ItemType Directory -Path $outputDirectory | Out-Null
    }
    Start-Transcript -Path $OutputPath -Force | Out-Null
}

$script:ExitCode = 0
try {
    Main
} finally {
    if ($OutputPath) {
        Stop-Transcript | Out-Null
        Write-Host "Diagnostics written to $OutputPath"
    }
}

exit $script:ExitCode
