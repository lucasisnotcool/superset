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
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "stop", "logs", "ps", "restart", "nuke", "ports", "dry-run")]
    [string]$Command = "up",

    [switch]$Detached,
    [switch]$Follow,
    [string]$Service
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFiles = @(
    "-f", "docker-compose.no-bind.yml",
    "-f", "docker-compose.ai-agent.yml"
)
$AiAgentEnvFile = Join-Path $RepoRoot "superset_ai_agent/.env"
$ProjectName = [regex]::Replace((Split-Path -Leaf $RepoRoot).ToLowerInvariant(), "[^a-z0-9]+", "-").Trim("-")

function Invoke-Compose {
    param([string[]]$ComposeArguments)

    Push-Location $RepoRoot
    try {
        & docker compose @ComposeFiles @ComposeArguments
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    } finally {
        Pop-Location
    }
}

function Invoke-DockerAllowFailure {
    param([string[]]$DockerArguments)

    $hasNativePreference = Test-Path Variable:\PSNativeCommandUseErrorActionPreference
    if ($hasNativePreference) {
        $previousNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    try {
        & docker @DockerArguments 2>$null
    } finally {
        if ($hasNativePreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativePreference
        }
    }
}

function Get-DockerArch {
    $arch = Invoke-DockerAllowFailure -DockerArguments @(
        "info",
        "--format",
        "{{.Architecture}}"
    )
    if (-not $arch) {
        $arch = Invoke-DockerAllowFailure -DockerArguments @(
            "version",
            "--format",
            "{{.Server.Arch}}"
        )
    }
    if (-not $arch) {
        return ""
    }
    return [string]$arch
}

function Set-PythonCompatibility {
    $existing = [Environment]::GetEnvironmentVariable("SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION")
    if ($null -ne $existing) {
        return
    }

    $arch = (Get-DockerArch).Trim().ToLowerInvariant()
    if ($arch -eq "aarch64" -or $arch -eq "arm64") {
        $env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION = "45.0.7"
    } else {
        $env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION = ""
    }
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = $null
    try {
        $address = [System.Net.IPAddress]::Parse("127.0.0.1")
        $listener = [System.Net.Sockets.TcpListener]::new($address, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Find-AvailablePort {
    param([int]$BasePort)

    for ($port = $BasePort; $port -lt ($BasePort + 100); $port++) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
    }

    throw "Could not find an available port starting from $BasePort"
}

function Read-AiAgentEnvValue {
    param([string]$Key)

    if (-not (Test-Path $AiAgentEnvFile)) {
        return $null
    }

    $value = $null
    foreach ($line in Get-Content $AiAgentEnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed.StartsWith("export ")) {
            $trimmed = $trimmed.Substring(7).Trim()
        }
        $equalsIndex = $trimmed.IndexOf("=")
        if ($equalsIndex -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $equalsIndex).Trim()
        if ($name -ne $Key) {
            continue
        }
        $value = $trimmed.Substring($equalsIndex + 1).Trim()
        $value = $value.Trim([char]34).Trim([char]39)
    }

    return $value
}

function Get-AiAgentConfigValue {
    param([string]$Key)

    $processValue = [Environment]::GetEnvironmentVariable($Key, "Process")
    if ($null -ne $processValue) {
        return $processValue
    }
    return Read-AiAgentEnvValue -Key $Key
}

function Test-FalseValue {
    param([string]$Value)

    $normalized = "$Value".Trim().ToLowerInvariant()
    return @("0", "false", "no", "off").Contains($normalized)
}

function Require-AiAgentConfig {
    param([string]$Key)

    $value = Get-AiAgentConfigValue -Key $Key
    if (-not $value) {
        throw "$Key must be set in superset_ai_agent/.env for Docker AI agent startup."
    }
}

function Test-AiAgentConfig {
    $adapter = Get-AiAgentConfigValue -Key "SUPERSET_AGENT_ADAPTER"
    if (-not $adapter) {
        $adapter = "rest"
    }
    $adapter = $adapter.Trim().ToLowerInvariant()

    switch ($adapter) {
        "rest" {}
        "mcp" {}
        "local" {
            throw "SUPERSET_AGENT_ADAPTER=local is not supported by the Docker AI agent smoke stack. Use SUPERSET_AGENT_ADAPTER=rest or mcp in superset_ai_agent/.env."
        }
        default {
            throw "SUPERSET_AGENT_ADAPTER must be rest or mcp for Docker startup."
        }
    }

    $provider = Get-AiAgentConfigValue -Key "AI_AGENT_MODEL_PROVIDER"
    if (-not $provider) {
        $provider = "openai_compatible"
    }
    $provider = $provider.Trim().ToLowerInvariant()

    switch ($provider) {
        "openai" {
            Require-AiAgentConfig -Key "OPENAI_API_KEY"
        }
        "openai_compatible" {
            Require-AiAgentConfig -Key "OPENAI_COMPATIBLE_BASE_URL"
            Require-AiAgentConfig -Key "OPENAI_COMPATIBLE_MODEL"
            $requireKey = Get-AiAgentConfigValue -Key "OPENAI_COMPATIBLE_REQUIRE_API_KEY"
            if (-not $requireKey) {
                $requireKey = "true"
            }
            if (-not (Test-FalseValue -Value $requireKey)) {
                Require-AiAgentConfig -Key "OPENAI_COMPATIBLE_API_KEY"
            }
        }
        "azure_openai" {
            Require-AiAgentConfig -Key "AZURE_OPENAI_ENDPOINT"
            Require-AiAgentConfig -Key "AZURE_OPENAI_KEY"
            Require-AiAgentConfig -Key "AZURE_OPENAI_MODEL"
            Require-AiAgentConfig -Key "AZURE_OPENAI_API_VERSION"
        }
        "ollama" {
            throw "Ollama is not supported by the Docker AI agent smoke stack. Use AI_AGENT_MODEL_PROVIDER=openai, openai_compatible, or azure_openai in superset_ai_agent/.env."
        }
        default {
            throw "AI_AGENT_MODEL_PROVIDER must be openai, openai_compatible, or azure_openai for Docker startup."
        }
    }
}

function Get-RunningSitePort {
    param([int]$Fallback)

    Push-Location $RepoRoot
    try {
        $dockerArguments = @("compose") + $ComposeFiles + @("port", "nginx", "80")
        $output = Invoke-DockerAllowFailure -DockerArguments $dockerArguments
        if ($LASTEXITCODE -eq 0 -and $output) {
            $lastLine = @($output)[-1]
            return [int]($lastLine -split ":")[-1]
        }
    } finally {
        Pop-Location
    }

    return $Fallback
}

function Test-ProjectRunning {
    Push-Location $RepoRoot
    try {
        $dockerArguments = @("compose") + $ComposeFiles + @("ps", "--status", "running")
        $output = Invoke-DockerAllowFailure -DockerArguments $dockerArguments
        return ($LASTEXITCODE -eq 0 -and (($output -join "`n") -match [regex]::Escape($ProjectName)))
    } finally {
        Pop-Location
    }
}

function Show-ConnectionInfo {
    Write-Host ""
    Write-Host "Superset + AI agent ($ProjectName):"
    Write-Host "   Site:     http://localhost:$env:NGINX_HOST_PORT"
    Write-Host "   AI proxy: http://localhost:$env:NGINX_HOST_PORT/ai-agent"
    Write-Host "   Internal services are reachable only on the Docker network."
    Write-Host "   Docker mode: packaged images, no host bind mounts."
    if ($env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION) {
        Write-Host "   Python compat: cryptography==$env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION"
    }
    Write-Host ""
}

$env:COMPOSE_PROJECT_NAME = $ProjectName

switch ($Command) {
    "down" {
        Invoke-Compose -ComposeArguments @("down")
        exit 0
    }
    "stop" {
        Invoke-Compose -ComposeArguments @("stop")
        exit 0
    }
    "ps" {
        Invoke-Compose -ComposeArguments @("ps")
        exit 0
    }
    "logs" {
        $composeArguments = @("logs")
        if ($Follow) {
            $composeArguments += "-f"
        }
        if ($Service) {
            $composeArguments += $Service
        }
        Invoke-Compose -ComposeArguments $composeArguments
        exit 0
    }
    "restart" {
        $composeArguments = @("restart")
        if ($Service) {
            $composeArguments += $Service
        }
        Invoke-Compose -ComposeArguments $composeArguments
        exit 0
    }
    "nuke" {
        Write-Host "Removing containers, volumes, and locally-built images for $ProjectName..."
        Invoke-Compose -ComposeArguments @("down", "-v", "--rmi", "local")
        exit 0
    }
}

Set-PythonCompatibility

if (-not (Test-Path $AiAgentEnvFile)) {
    throw "superset_ai_agent/.env is required. Create it with: Copy-Item superset_ai_agent/.env.example superset_ai_agent/.env. If you already have docker/.env-ai-agent, move those values into superset_ai_agent/.env."
}

$env:NGINX_HOST_PORT = [string](Find-AvailablePort -BasePort 8090)
if (Test-ProjectRunning) {
    $env:NGINX_HOST_PORT = [string](Get-RunningSitePort -Fallback ([int]$env:NGINX_HOST_PORT))
}

Show-ConnectionInfo

switch ($Command) {
    "dry-run" {
        Write-Host "Dry run complete. To start, run with Command=up."
        exit 0
    }
    "ports" {
        exit 0
    }
    default {
        Test-AiAgentConfig
        $composeArguments = @("up", "--build")
        if ($Detached) {
            $composeArguments += "-d"
        }
        Invoke-Compose -ComposeArguments $composeArguments
    }
}
