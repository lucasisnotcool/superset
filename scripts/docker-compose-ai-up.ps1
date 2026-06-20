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
    [ValidateSet("up", "down", "stop", "logs", "ps", "restart", "nuke", "ports", "env", "dry-run")]
    [string]$Command = "up",

    [switch]$Detached,
    [switch]$Follow,
    [string]$Service
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.ai-agent.yml")
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

function Find-ConsecutivePortBlock {
    param(
        [int]$BasePort,
        [int]$Count
    )

    for ($start = $BasePort; $start -lt ($BasePort + 100); $start++) {
        $available = $true
        for ($offset = 0; $offset -lt $Count; $offset++) {
            if (-not (Test-PortAvailable -Port ($start + $offset))) {
                $available = $false
                break
            }
        }
        if ($available) {
            return $start
        }
    }

    throw "Could not find $Count consecutive available ports starting from $BasePort"
}

function Set-ConsecutivePorts {
    param([int]$BasePort)

    $env:NGINX_PORT = [string]$BasePort
    $env:SUPERSET_PORT = [string]($BasePort + 1)
    $env:NODE_PORT = [string]($BasePort + 2)
    $env:WEBSOCKET_PORT = [string]($BasePort + 3)
    $env:CYPRESS_PORT = [string]($BasePort + 4)
    $env:DATABASE_HOST_PORT = [string]($BasePort + 5)
    $env:REDIS_HOST_PORT = [string]($BasePort + 6)
    $env:AI_AGENT_PORT = [string]($BasePort + 7)
}

function Read-AiAgentEnvValue {
    param([string]$Key)

    $envFile = Join-Path $RepoRoot "docker/.env-ai-agent"
    if (-not (Test-Path $envFile)) {
        return $null
    }

    $value = $null
    foreach ($line in Get-Content $envFile) {
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
        throw "$Key must be set in docker/.env-ai-agent for Docker AI agent startup."
    }
}

function Test-AiAgentConfig {
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
            throw "Ollama is not supported by the Docker AI agent smoke stack. Use AI_AGENT_MODEL_PROVIDER=openai, openai_compatible, or azure_openai in docker/.env-ai-agent."
        }
        default {
            throw "AI_AGENT_MODEL_PROVIDER must be openai, openai_compatible, or azure_openai for Docker startup."
        }
    }
}

function Get-RunningPort {
    param(
        [string]$ComposeService,
        [int]$ContainerPort,
        [int]$Fallback
    )

    Push-Location $RepoRoot
    try {
        $dockerArguments = @("compose") + $ComposeFiles + @(
            "port",
            $ComposeService,
            [string]$ContainerPort
        )
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
        $dockerArguments = @("compose") + $ComposeFiles + @(
            "ps",
            "--status",
            "running"
        )
        $output = Invoke-DockerAllowFailure -DockerArguments $dockerArguments
        return ($LASTEXITCODE -eq 0 -and (($output -join "`n") -match [regex]::Escape($ProjectName)))
    } finally {
        Pop-Location
    }
}

function Show-ConnectionInfo {
    Write-Host ""
    Write-Host "Superset + AI agent ($ProjectName):"
    Write-Host "   Dev Server: http://localhost:$env:NODE_PORT"
    Write-Host "   Superset:   http://localhost:$env:SUPERSET_PORT"
    Write-Host "   Nginx:      http://localhost:$env:NGINX_PORT"
    Write-Host "   AI Agent:   http://localhost:$env:AI_AGENT_PORT"
    Write-Host "   AI Proxy:   http://localhost:$env:NODE_PORT/ai-agent"
    Write-Host "   WebSocket:  localhost:$env:WEBSOCKET_PORT"
    Write-Host "   Cypress:    http://localhost:$env:CYPRESS_PORT"
    Write-Host "   Database:   localhost:$env:DATABASE_HOST_PORT"
    Write-Host "   Redis:      localhost:$env:REDIS_HOST_PORT"
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

Write-Host "Finding available ports for Superset + AI agent..."
Set-PythonCompatibility

$portBase = Find-ConsecutivePortBlock -BasePort 8080 -Count 8
Set-ConsecutivePorts -BasePort $portBase

$envFile = Join-Path $RepoRoot "docker/.env-ai-agent"
if (-not (Test-Path $envFile)) {
    throw "docker/.env-ai-agent is required. Create it with: Copy-Item docker/.env-ai-agent.example docker/.env-ai-agent"
}

if (Test-ProjectRunning) {
    $env:NGINX_PORT = [string](Get-RunningPort -ComposeService "nginx" -ContainerPort 80 -Fallback ([int]$env:NGINX_PORT))
    $env:SUPERSET_PORT = [string](Get-RunningPort -ComposeService "superset" -ContainerPort 8088 -Fallback ([int]$env:SUPERSET_PORT))
    $env:NODE_PORT = [string](Get-RunningPort -ComposeService "superset-node" -ContainerPort 9000 -Fallback ([int]$env:NODE_PORT))
    $env:WEBSOCKET_PORT = [string](Get-RunningPort -ComposeService "superset-websocket" -ContainerPort 8080 -Fallback ([int]$env:WEBSOCKET_PORT))
    $env:DATABASE_HOST_PORT = [string](Get-RunningPort -ComposeService "db" -ContainerPort 5432 -Fallback ([int]$env:DATABASE_HOST_PORT))
    $env:REDIS_HOST_PORT = [string](Get-RunningPort -ComposeService "redis" -ContainerPort 6379 -Fallback ([int]$env:REDIS_HOST_PORT))
    $env:AI_AGENT_PORT = [string](Get-RunningPort -ComposeService "superset-ai-agent" -ContainerPort 5050 -Fallback ([int]$env:AI_AGENT_PORT))
}

Show-ConnectionInfo

switch ($Command) {
    "dry-run" {
        Write-Host "Dry run complete. To start, run with Command=up."
        exit 0
    }
    "env" {
        Write-Host "`$env:COMPOSE_PROJECT_NAME = `"$ProjectName`""
        Write-Host "`$env:NGINX_PORT = `"$env:NGINX_PORT`""
        Write-Host "`$env:SUPERSET_PORT = `"$env:SUPERSET_PORT`""
        Write-Host "`$env:NODE_PORT = `"$env:NODE_PORT`""
        Write-Host "`$env:WEBSOCKET_PORT = `"$env:WEBSOCKET_PORT`""
        Write-Host "`$env:CYPRESS_PORT = `"$env:CYPRESS_PORT`""
        Write-Host "`$env:DATABASE_HOST_PORT = `"$env:DATABASE_HOST_PORT`""
        Write-Host "`$env:REDIS_HOST_PORT = `"$env:REDIS_HOST_PORT`""
        Write-Host "`$env:AI_AGENT_PORT = `"$env:AI_AGENT_PORT`""
        Write-Host "`$env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION = `"$env:SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION`""
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
