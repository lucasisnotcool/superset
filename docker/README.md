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

To customize the WebSocket server configuration, create `./docker/superset-websocket/config.json` (git-ignored) based on [`./docker/superset-websocket/config.example.json`](./superset-websocket/config.example.json).

Then update the `superset-websocket`.`volumes` config to mount it.

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

The database will initialize itself upon startup via the init container ([`superset-init`](./docker-init.sh)). This may take a minute.

## Normal Operation

To run the container, simply run: `docker compose up`

After waiting several minutes for Superset initialization to finish, open
[`http://localhost:8092`](http://localhost:8092) for the frontend development
server. The default Docker host port block is:

| Service | Host URL or port | Injected by |
| --- | --- | --- |
| Nginx | `http://localhost:8090` | `NGINX_HOST_PORT` |
| Superset backend | `http://localhost:8091` | `SUPERSET_HOST_PORT` |
| Frontend dev server | `http://localhost:8092` | `NODE_HOST_PORT` |
| WebSocket | `localhost:8093` | `WEBSOCKET_HOST_PORT` |
| Cypress backend | `http://localhost:8094` | `CYPRESS_HOST_PORT` |
| Database | `localhost:8095` | `DATABASE_HOST_PORT` |
| Redis | `localhost:8096` | `REDIS_HOST_PORT` |
| AI agent with `docker-compose.ai-agent.yml` | `http://localhost:8097` | `AI_AGENT_HOST_PORT` |

Container-internal service ports stay at their defaults, such as Superset
`8088`, webpack `9000`, Postgres `5432`, and Redis `6379`.

### Running Multiple Instances

If you need to run multiple Superset instances simultaneously (e.g., different branches or clones), use the make targets which automatically find available ports:

```bash
make up
```

This automatically:
- Generates a unique project name from your directory
- Finds available ports starting with the `8090` host port block
- Displays the assigned URLs before starting

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

## Production

It is possible to run Superset in non-development mode by using [`docker-compose-non-dev.yml`](../docker-compose-non-dev.yml). This file excludes the volumes needed for development.

## Resource Constraints

If you are attempting to build on macOS and it exits with 137 you need to increase your Docker resources. See instructions [here](https://docs.docker.com/docker-for-mac/#advanced) (search for memory)
