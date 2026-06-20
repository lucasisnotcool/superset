#!/usr/bin/env bash
#
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
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.ai-agent.yml)

DIR_NAME=$(basename "$REPO_ROOT")
PROJECT_NAME=$(echo "$DIR_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

detect_docker_arch() {
    local arch
    arch=$(docker info --format '{{.Architecture}}' 2>/dev/null || true)
    if [[ -z "$arch" ]]; then
        arch=$(docker version --format '{{.Server.Arch}}' 2>/dev/null || true)
    fi
    if [[ -z "$arch" ]]; then
        arch=$(uname -m)
    fi
    echo "$arch"
}

configure_python_compatibility() {
    local docker_arch
    if [[ -n "${SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION+x}" ]]; then
        return
    fi

    docker_arch=$(detect_docker_arch)
    case "$docker_arch" in
        aarch64|arm64)
            SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION="45.0.7"
            ;;
        *)
            SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION=""
            ;;
    esac
}

is_port_available() {
    local port=$1
    if command -v python3 &> /dev/null; then
        python3 - "$port" <<'PY'
import errno
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError as ex:
    sys.exit(1 if ex.errno == errno.EADDRINUSE else 0)
finally:
    sock.close()
PY
    elif command -v python &> /dev/null; then
        python - "$port" <<'PY'
import errno
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError as ex:
    sys.exit(1 if ex.errno == errno.EADDRINUSE else 0)
finally:
    sock.close()
PY
    elif command -v lsof &> /dev/null; then
        ! lsof -i ":$port" &> /dev/null
    elif command -v netstat &> /dev/null; then
        ! netstat -tuln 2>/dev/null | grep -q ":$port "
    elif command -v ss &> /dev/null; then
        ! ss -tuln 2>/dev/null | grep -q ":$port "
    else
        return 0
    fi
}

find_consecutive_port_block() {
    local base_port=$1
    local count=$2
    local max_attempts=100
    local start
    local offset
    local available

    for ((start=base_port; start<base_port+max_attempts; start++)); do
        available=1
        for ((offset=0; offset<count; offset++)); do
            if ! is_port_available "$((start + offset))"; then
                available=0
                break
            fi
        done
        if [[ "$available" == "1" ]]; then
            echo "$start"
            return 0
        fi
    done

    echo "ERROR: Could not find $count consecutive available ports starting from $base_port" >&2
    return 1
}

set_consecutive_ports() {
    local base_port=$1

    NGINX_HOST_PORT=$base_port
    SUPERSET_HOST_PORT=$((base_port + 1))
    NODE_HOST_PORT=$((base_port + 2))
    WEBSOCKET_HOST_PORT=$((base_port + 3))
    CYPRESS_HOST_PORT=$((base_port + 4))
    DATABASE_HOST_PORT=$((base_port + 5))
    REDIS_HOST_PORT=$((base_port + 6))
    AI_AGENT_HOST_PORT=$((base_port + 7))
}

case "${1:-}" in
    down|stop|logs|ps|exec|restart)
        export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
        cd "$REPO_ROOT"
        docker compose "${COMPOSE_FILES[@]}" "$@"
        exit 0
        ;;
    nuke)
        export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
        cd "$REPO_ROOT"
        echo "Removing containers, volumes, and locally-built images for $PROJECT_NAME..."
        docker compose "${COMPOSE_FILES[@]}" down -v --rmi local
        exit 0
        ;;
esac

echo "Finding available ports for Superset + AI agent..."
configure_python_compatibility
PORT_BASE=$(find_consecutive_port_block 8090 8)
set_consecutive_ports "$PORT_BASE"

export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
export NGINX_HOST_PORT
export SUPERSET_HOST_PORT
export NODE_HOST_PORT
export WEBSOCKET_HOST_PORT
export CYPRESS_HOST_PORT
export DATABASE_HOST_PORT
export REDIS_HOST_PORT
export AI_AGENT_HOST_PORT
export SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION

cd "$REPO_ROOT"

if [ ! -f docker/.env-ai-agent ]; then
    echo "ERROR: docker/.env-ai-agent is required." >&2
    echo "Create it with: cp docker/.env-ai-agent.example docker/.env-ai-agent" >&2
    echo "Then fill in OpenAI or OpenAI-compatible credentials." >&2
    exit 1
fi

read_ai_agent_env_value() {
    local key=$1
    local value
    value=$(awk -v key="$key" '
        /^[[:space:]]*(#|$)/ { next }
        {
            line = $0
            sub(/^[[:space:]]*export[[:space:]]+/, "", line)
            split(line, parts, "=")
            name = parts[1]
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
            if (name == key) {
                value = substr(line, index(line, "=") + 1)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
            }
        }
    ' docker/.env-ai-agent | tail -n 1)
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    echo "$value"
}

ai_agent_config_value() {
    local key=$1
    if [[ -n "${!key+x}" ]]; then
        echo "${!key}"
    else
        read_ai_agent_env_value "$key"
    fi
}

is_false_value() {
    local value
    value=$(echo "$1" | tr '[:upper:]' '[:lower:]')
    [[ "$value" == "0" || "$value" == "false" || "$value" == "no" || "$value" == "off" ]]
}

require_ai_agent_config() {
    local key=$1
    local value
    value=$(ai_agent_config_value "$key")
    if [[ -z "$value" ]]; then
        echo "ERROR: $key must be set in docker/.env-ai-agent for Docker AI agent startup." >&2
        return 1
    fi
}

validate_ai_agent_config() {
    local provider
    local require_key
    provider=$(ai_agent_config_value AI_AGENT_MODEL_PROVIDER)
    provider="${provider:-openai_compatible}"

    case "$provider" in
        openai)
            require_ai_agent_config OPENAI_API_KEY
            ;;
        openai_compatible)
            require_ai_agent_config OPENAI_COMPATIBLE_BASE_URL
            require_ai_agent_config OPENAI_COMPATIBLE_MODEL
            require_key=$(ai_agent_config_value OPENAI_COMPATIBLE_REQUIRE_API_KEY)
            if ! is_false_value "${require_key:-true}"; then
                require_ai_agent_config OPENAI_COMPATIBLE_API_KEY
            fi
            ;;
        azure_openai)
            require_ai_agent_config AZURE_OPENAI_ENDPOINT
            require_ai_agent_config AZURE_OPENAI_KEY
            require_ai_agent_config AZURE_OPENAI_MODEL
            require_ai_agent_config AZURE_OPENAI_API_VERSION
            ;;
        ollama)
            echo "ERROR: Ollama is not supported by the Docker AI agent smoke stack." >&2
            echo "Use AI_AGENT_MODEL_PROVIDER=openai, openai_compatible, or azure_openai in docker/.env-ai-agent." >&2
            return 1
            ;;
        *)
            echo "ERROR: AI_AGENT_MODEL_PROVIDER must be openai, openai_compatible, or azure_openai for Docker startup." >&2
            return 1
            ;;
    esac
}

get_running_port() {
    local service=$1
    local container_port=$2
    local fallback=$3
    local running_port
    running_port=$(docker compose "${COMPOSE_FILES[@]}" port "$service" "$container_port" 2>/dev/null | cut -d: -f2)
    if [[ -n "$running_port" ]]; then
        echo "$running_port"
    else
        echo "$fallback"
    fi
}

if docker compose "${COMPOSE_FILES[@]}" ps --status running 2>/dev/null | grep -q "$PROJECT_NAME"; then
    NGINX_HOST_PORT=$(get_running_port nginx 80 "$NGINX_HOST_PORT")
    SUPERSET_HOST_PORT=$(get_running_port superset 8088 "$SUPERSET_HOST_PORT")
    NODE_HOST_PORT=$(get_running_port superset-node 9000 "$NODE_HOST_PORT")
    WEBSOCKET_HOST_PORT=$(get_running_port superset-websocket 8080 "$WEBSOCKET_HOST_PORT")
    DATABASE_HOST_PORT=$(get_running_port db 5432 "$DATABASE_HOST_PORT")
    REDIS_HOST_PORT=$(get_running_port redis 6379 "$REDIS_HOST_PORT")
    AI_AGENT_HOST_PORT=$(get_running_port superset-ai-agent 5050 "$AI_AGENT_HOST_PORT")
fi

print_connection_info() {
    echo ""
    echo "Superset + AI agent ($PROJECT_NAME):"
    echo "   Dev Server: http://localhost:$NODE_HOST_PORT"
    echo "   Superset:   http://localhost:$SUPERSET_HOST_PORT"
    echo "   Nginx:      http://localhost:$NGINX_HOST_PORT"
    echo "   AI Agent:   http://localhost:$AI_AGENT_HOST_PORT"
    echo "   AI Proxy:   http://localhost:$NODE_HOST_PORT/ai-agent"
    echo "   WebSocket:  localhost:$WEBSOCKET_HOST_PORT"
    echo "   Cypress:    http://localhost:$CYPRESS_HOST_PORT"
    echo "   Database:   localhost:$DATABASE_HOST_PORT"
    echo "   Redis:      localhost:$REDIS_HOST_PORT"
    if [[ -n "$SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION" ]]; then
        echo "   Python compat: cryptography==$SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION"
    fi
    echo ""
}

print_connection_info

case "${1:-}" in
    --dry-run)
        echo "Dry run complete. To start, run without --dry-run."
        exit 0
        ;;
    --env)
        echo "export COMPOSE_PROJECT_NAME='$PROJECT_NAME'"
        echo "export NGINX_HOST_PORT=$NGINX_HOST_PORT"
        echo "export SUPERSET_HOST_PORT=$SUPERSET_HOST_PORT"
        echo "export NODE_HOST_PORT=$NODE_HOST_PORT"
        echo "export WEBSOCKET_HOST_PORT=$WEBSOCKET_HOST_PORT"
        echo "export CYPRESS_HOST_PORT=$CYPRESS_HOST_PORT"
        echo "export DATABASE_HOST_PORT=$DATABASE_HOST_PORT"
        echo "export REDIS_HOST_PORT=$REDIS_HOST_PORT"
        echo "export AI_AGENT_HOST_PORT=$AI_AGENT_HOST_PORT"
        echo "export SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION='$SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION'"
        exit 0
        ;;
    ports)
        exit 0
        ;;
    *)
        validate_ai_agent_config
        trap 'echo ""; print_connection_info' EXIT
        docker compose "${COMPOSE_FILES[@]}" up --build "$@"
        ;;
esac
