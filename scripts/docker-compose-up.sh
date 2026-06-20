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

# -----------------------------------------------------------------------
# Smart docker-compose wrapper for running multiple Superset instances
#
# Features:
#   - Auto-generates unique project name from directory
#   - Finds available ports automatically
#   - No manual .env-local editing needed
#
# Usage:
#   ./scripts/docker-compose-up.sh [docker-compose args...]
#
# Examples:
#   ./scripts/docker-compose-up.sh           # Start all services
#   ./scripts/docker-compose-up.sh -d        # Start detached
#   ./scripts/docker-compose-up.sh down      # Stop services
# -----------------------------------------------------------------------

set -e

# Get the repo root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Generate project name from directory name (sanitized for Docker)
DIR_NAME=$(basename "$REPO_ROOT")
PROJECT_NAME=$(echo "$DIR_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

# Function to check if a port is available
is_port_available() {
    local port=$1
    if command -v lsof &> /dev/null; then
        ! lsof -i ":$port" &> /dev/null
    elif command -v netstat &> /dev/null; then
        ! netstat -tuln 2>/dev/null | grep -q ":$port "
    elif command -v ss &> /dev/null; then
        ! ss -tuln 2>/dev/null | grep -q ":$port "
    else
        # If no tool available, assume port is available
        return 0
    fi
}

# Function to find a consecutive block of available host ports
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

    NGINX_PORT=$base_port
    SUPERSET_PORT=$((base_port + 1))
    NODE_PORT=$((base_port + 2))
    WEBSOCKET_PORT=$((base_port + 3))
    CYPRESS_PORT=$((base_port + 4))
    DATABASE_HOST_PORT=$((base_port + 5))
    REDIS_HOST_PORT=$((base_port + 6))
}

# Find available ports (no subshells - claims persist correctly)
echo "🔍 Finding available ports..."
PORT_BASE=$(find_consecutive_port_block 8080 7)
set_consecutive_ports "$PORT_BASE"

# Export for docker-compose
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

# Function to get port from running container, or use the found available port
get_running_port() {
    local service=$1
    local container_port=$2
    local fallback=$3
    local running_port=$(docker compose port "$service" "$container_port" 2>/dev/null | cut -d: -f2)
    if [[ -n "$running_port" ]]; then
        echo "$running_port"
    else
        echo "$fallback"
    fi
}

# Check if containers are running and get actual ports, otherwise use available ports
cd "$REPO_ROOT"
if docker compose ps --status running 2>/dev/null | grep -q "$PROJECT_NAME"; then
    # Containers are running - get actual ports
    NGINX_PORT=$(get_running_port nginx 80 $NGINX_PORT)
    SUPERSET_PORT=$(get_running_port superset 8088 $SUPERSET_PORT)
    NODE_PORT=$(get_running_port superset-node 9000 $NODE_PORT)
    WEBSOCKET_PORT=$(get_running_port superset-websocket 8080 $WEBSOCKET_PORT)
    DATABASE_HOST_PORT=$(get_running_port db 5432 $DATABASE_HOST_PORT)
    REDIS_HOST_PORT=$(get_running_port redis 6379 $REDIS_HOST_PORT)
fi

export NGINX_PORT
export SUPERSET_PORT
export NODE_PORT
export WEBSOCKET_PORT
export CYPRESS_PORT
export DATABASE_HOST_PORT
export REDIS_HOST_PORT

# Function to print connection info
print_connection_info() {
    echo ""
    echo "🐳 Superset ($PROJECT_NAME):"
    echo "   Dev Server: http://localhost:$NODE_PORT  ← Use this for development"
    echo "   Superset:   http://localhost:$SUPERSET_PORT"
    echo "   Nginx:      http://localhost:$NGINX_PORT"
    echo "   WebSocket:  localhost:$WEBSOCKET_PORT"
    echo "   Cypress:    http://localhost:$CYPRESS_PORT"
    echo "   Database:   localhost:$DATABASE_HOST_PORT"
    echo "   Redis:      localhost:$REDIS_HOST_PORT"
    echo ""
}

# Function to open browser (macOS/Linux compatible)
open_browser() {
    local url="http://localhost:$NODE_PORT"
    if command -v open &> /dev/null; then
        open "$url"  # macOS
    elif command -v xdg-open &> /dev/null; then
        xdg-open "$url"  # Linux
    else
        echo "Open in browser: $url"
    fi
}

print_connection_info

# Handle special commands
case "${1:-}" in
    --dry-run)
        echo "✅ Dry run complete. To start, run without --dry-run"
        exit 0
        ;;
    --env)
        # Output as sourceable environment variables
        echo "export COMPOSE_PROJECT_NAME='$PROJECT_NAME'"
        echo "export NGINX_PORT=$NGINX_PORT"
        echo "export SUPERSET_PORT=$SUPERSET_PORT"
        echo "export NODE_PORT=$NODE_PORT"
        echo "export WEBSOCKET_PORT=$WEBSOCKET_PORT"
        echo "export CYPRESS_PORT=$CYPRESS_PORT"
        echo "export DATABASE_HOST_PORT=$DATABASE_HOST_PORT"
        echo "export REDIS_HOST_PORT=$REDIS_HOST_PORT"
        exit 0
        ;;
    ports)
        # Just show the ports (already printed above)
        exit 0
        ;;
    open)
        # Open browser to the dev server
        echo "🌐 Opening browser..."
        open_browser
        exit 0
        ;;
    down|stop|logs|ps|exec|restart)
        # Pass through to docker compose
        docker compose "$@"
        ;;
    nuke)
        # Nuclear option: remove everything (containers, volumes, local images)
        echo "💥 Nuking all containers, volumes, and locally-built images for $PROJECT_NAME..."
        docker compose down -v --rmi local
        echo "✅ Done. Run 'make up' or './scripts/docker-compose-up.sh' to start fresh."
        ;;
    *)
        # Default: start services
        # Print connection info again when user exits (Ctrl+C)
        trap 'echo ""; print_connection_info; echo "Run '\''make open'\'' to open browser, '\''make ports'\'' to see ports"' EXIT
        docker compose up "$@"
        ;;
esac
