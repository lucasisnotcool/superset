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

DIR_NAME=$(basename "$REPO_ROOT")
PROJECT_NAME=$(echo "$DIR_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

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

find_available_port() {
    local base_port=$1
    local max_attempts=100
    local port

    for ((port=base_port; port<base_port+max_attempts; port++)); do
        if is_port_available "$port"; then
            echo "$port"
            return 0
        fi
    done

    echo "ERROR: Could not find an available port starting from $base_port" >&2
    return 1
}

get_running_port() {
    local fallback=$1
    local running_port
    running_port=$(docker compose port nginx 80 2>/dev/null | cut -d: -f2)
    if [[ -n "$running_port" ]]; then
        echo "$running_port"
    else
        echo "$fallback"
    fi
}

print_connection_info() {
    echo ""
    echo "Superset ($PROJECT_NAME):"
    echo "   Site: http://localhost:$NGINX_HOST_PORT"
    echo "   Internal services are reachable only on the Docker network."
    echo ""
}

open_browser() {
    local url="http://localhost:$NGINX_HOST_PORT"
    if command -v open &> /dev/null; then
        open "$url"
    elif command -v xdg-open &> /dev/null; then
        xdg-open "$url"
    else
        echo "Open in browser: $url"
    fi
}

export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
cd "$REPO_ROOT"

case "${1:-}" in
    down|stop|logs|ps|exec|restart)
        docker compose "$@"
        exit 0
        ;;
    nuke)
        echo "Removing containers, volumes, and locally-built images for $PROJECT_NAME..."
        docker compose down -v --rmi local
        exit 0
        ;;
esac

NGINX_HOST_PORT=$(find_available_port 8090)
if docker compose ps --status running 2>/dev/null | grep -q "$PROJECT_NAME"; then
    NGINX_HOST_PORT=$(get_running_port "$NGINX_HOST_PORT")
fi
export NGINX_HOST_PORT

print_connection_info

case "${1:-}" in
    --dry-run)
        echo "Dry run complete. To start, run without --dry-run."
        ;;
    ports)
        ;;
    open)
        open_browser
        ;;
    *)
        trap 'echo ""; print_connection_info' EXIT
        docker compose up "$@"
        ;;
esac
