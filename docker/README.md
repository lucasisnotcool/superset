<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Getting Started with Superset using Docker

Docker is an easy way to get started with Superset.

## Prerequisites

1. [Docker](https://www.docker.com/get-started)
2. [Docker Compose](https://docs.docker.com/compose/install/)

## Configuration

The `/app/pythonpath` folder is mounted from [`./docker/pythonpath_dev`](./pythonpath_dev)
which contains a base configuration [`./docker/pythonpath_dev/superset_config.py`](./pythonpath_dev/superset_config.py)
intended for use with local development.

### Local overrides

#### Environment Variables

To override environment variables locally, create a `./docker/.env-local` file (git-ignored). This file will be loaded after `.env` and can override any settings.

#### Python Configuration

In order to override configuration settings locally, simply make a copy of [`./docker/pythonpath_dev/superset_config_local.example`](./pythonpath_dev/superset_config_local.example)
into `./docker/pythonpath_dev/superset_config_docker.py` (git-ignored) and fill in your overrides.

#### WebSocket Configuration

The Docker Compose stack configures the WebSocket server through the
`superset-websocket.environment` entries in [`../docker-compose.yml`](../docker-compose.yml).
It does not mount a host-side `config.json`.

For local WebSocket changes, use a `docker-compose-override.yml` file and
override the `superset-websocket.environment` values there.

#### Docker Compose Overrides

For advanced Docker Compose customization, create a `docker-compose-override.yml` file (git-ignored) to override or extend services without modifying the main compose file.

### Local packages

If you want to add Python packages in order to test things like databases locally, you can simply add a local requirements.txt (`./docker/requirements-local.txt`)
and rebuild your Docker stack.

Steps:

1. Create `./docker/requirements-local.txt`
2. Add your new packages
3. Rebuild docker compose
    1. `docker compose down -v`
    2. `docker compose up`

## Initializing Database

The database will initialize itself upon startup via the init container
([`superset-init`](./docker-init.sh)). The Postgres image used by the compose
files includes the scripts from [`./docker-entrypoint-initdb.d`](./docker-entrypoint-initdb.d),
so compose does not need to bind-mount those scripts from the host. This may
take a minute.

## Normal Operation

To run the container, simply run: `docker compose up`

After waiting several minutes for Superset initialization to finish, open
[`http://localhost:8090`](http://localhost:8090) for the nginx-served
development site. Docker Compose publishes only this one host port by default;
all other services communicate on the internal Docker network.

| Service | Address |
| --- | --- |
| Public site | `http://localhost:8090` |
| Superset backend | `superset:8088` inside Docker |
| Frontend dev server | `superset-node:9000` inside Docker |
| WebSocket | `superset-websocket:8080` inside Docker |
| Database | `db:5432` inside Docker |
| Redis | `redis:6379` inside Docker |
| AI agent with `docker-compose.ai-agent.yml` | `superset-ai-agent:5050` inside Docker, proxied at `/ai-agent` |

Container-internal service ports stay at their defaults, such as Superset
`8088`, webpack `9000`, Postgres `5432`, and Redis `6379`.

Nginx reaches services through Docker DNS by default:
`SUPERSET_APP_UPSTREAM=superset:8088`,
`SUPERSET_NODE_UPSTREAM=superset-node:9000`, and
`SUPERSET_WEBSOCKET_UPSTREAM=superset-websocket:8080`.

The helper scripts export the selected `NGINX_HOST_PORT`. If you override the
site port and run plain `docker compose` commands, pass the override file with
`--env-file docker/.env-local` so Compose uses it for port interpolation.

### Running Multiple Instances

If you need to run multiple Superset instances simultaneously (e.g., different branches or clones), use the make targets which automatically find available ports:

```bash
make up
```

This automatically:
- Generates a unique project name from your directory
- Finds an available site port starting with `8090`
- Displays the assigned site URL before starting

Available commands (run from repo root):

| Command | Description |
|---------|-------------|
| `make up` | Start services (foreground) |
| `make up-detached` | Start services (background) |
| `make down` | Stop all services |
| `make ps` | Show running containers |
| `make logs` | Follow container logs |
| `make nuke` | Stop, remove volumes & local images |

From a subdirectory, use: `make -C $(git rev-parse --show-toplevel) up`

**Important**: Always use these commands instead of plain `docker compose down`, which won't know the correct project name.

## Developing

While running, the container server will reload on modification of the Superset Python and JavaScript source code.
Don't forget to reload the page to take the new frontend into account though.

On Windows environments that cannot run host bind mounts reliably, use the
Windows PowerShell AI helper. It includes
[`../docker-compose.no-bind.yml`](../docker-compose.no-bind.yml), which runs the
stack from code packaged into images instead of mounted from the host. Rebuild
the stack after local source or Docker config changes.

## Production

It is possible to run Superset in non-development mode by using [`docker-compose-non-dev.yml`](../docker-compose-non-dev.yml). This file excludes the volumes needed for development.

## Resource Constraints

If you are attempting to build on macOS and it exits with 137 you need to increase your Docker resources. See instructions [here](https://docs.docker.com/docker-for-mac/#advanced) (search for memory)
