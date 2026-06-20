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

# Superset AI Agent POC on macOS

Standalone Python API for lightweight conversational database assistance and
text-to-SQL using LangGraph and a pluggable model provider.

This document contains macOS and Bash instructions. Windows PowerShell
instructions are in [README.md](README.md).

## Architecture

```text
Client / Superset extension
  -> FastAPI service
  -> LangGraph conversation / text-to-SQL workflows
  -> Ollama / OpenAI / OpenAI-compatible / Azure OpenAI model
  -> SupersetClient adapter
  -> Superset metadata and governed SQL execution
```

The Superset integration boundary is documented in
`integrations/superset/README.md`.

For a file-by-file architecture map, runtime diagrams, agent endpoints,
Superset REST/MCP surfaces, and SQL robustness extension points, see
`ARCHITECTURE.md`.

## macOS Bash Fresh Setup

Run these commands from the repository root.

### Docker Smoke

Docker smoke tests should use OpenAI, Azure OpenAI, or an OpenAI-compatible
provider. Do not configure Ollama in `docker/.env-ai-agent`.

Create the Docker agent env file and edit it:

```bash
cp docker/.env-ai-agent.example docker/.env-ai-agent
nano docker/.env-ai-agent
```

For an OpenAI-compatible gateway, set these values in
`docker/.env-ai-agent`:

```env
AI_AGENT_MODEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-gateway.example.com/v1
OPENAI_COMPATIBLE_API_KEY=your_key
OPENAI_COMPATIBLE_MODEL=your_model
OPENAI_COMPATIBLE_REQUIRE_API_KEY=true
OPENAI_COMPATIBLE_STRUCTURED_OUTPUT=json_schema
```

For a no-auth local gateway, set this in the same file:

```env
OPENAI_COMPATIBLE_REQUIRE_API_KEY=false
```

For direct OpenAI, set:

```env
AI_AGENT_MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
```

For Azure OpenAI, set `AZURE_OPENAI_MODEL` to the Azure deployment name:

```env
AI_AGENT_MODEL_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_KEY=your_key
AZURE_OPENAI_MODEL=your-deployment-name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_STRUCTURED_OUTPUT=json_schema
```

Start Superset, the frontend dev server, nginx, and the standalone agent:

```bash
./scripts/docker-compose-ai-up.sh -d
./scripts/docker-compose-ai-up.sh ps
```

The helper validates `docker/.env-ai-agent`, finds a consecutive host port
block starting at `8090` when available, and prints the actual URLs. The
default AI smoke block is:

```text
Nginx: http://localhost:8090
Superset: http://localhost:8091
Frontend dev server / proxy: http://localhost:8092
WebSocket: localhost:8093
Cypress backend: http://localhost:8094
Database: localhost:8095
Redis: localhost:8096
AI Agent: http://localhost:8097
AI Proxy: http://localhost:8092/ai-agent
```

If that block is busy, the helper shifts the entire block together. Use the
printed ports when they differ from the defaults.

On ARM64 Docker engines the helper also applies the Superset Python
compatibility override needed for the pinned dependency set. x86 Linux and
Windows Docker engines keep the normal pinned dependency set unless
`SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION` is set outside the helper.

Smoke-test the service:

```bash
curl http://localhost:8097/health
curl http://localhost:8092/ai-agent/health
```

If the script printed another block, use the printed frontend and AI agent
ports:

```bash
curl http://localhost:<NODE_HOST_PORT>/ai-agent/health
curl http://localhost:<AI_AGENT_HOST_PORT>/health
```

Bash helper commands:

```bash
./scripts/docker-compose-ai-up.sh --dry-run
./scripts/docker-compose-ai-up.sh ports
./scripts/docker-compose-ai-up.sh ps
./scripts/docker-compose-ai-up.sh logs -f superset-ai-agent
./scripts/docker-compose-ai-up.sh restart superset
./scripts/docker-compose-ai-up.sh down
```

### Native Dev

Use native dev when changing the AI agent or frontend. Ollama is supported only
for local native development.

Install Python dependencies:

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-ai-agent.txt
```

Create the native agent env file and edit it:

```bash
cp superset_ai_agent/.env.example .env.ai-agent
nano .env.ai-agent
```

Keep the native Superset and frontend URLs aligned with the 809x local port
block:

```env
SUPERSET_BASE_URL=http://localhost:8091
AI_AGENT_CORS_ALLOWED_ORIGINS=http://localhost:8091,http://127.0.0.1:8091,http://localhost:8092,http://127.0.0.1:8092
```

If you are running the Superset backend natively too:

```bash
python -m pip install -r requirements/development.txt
python -m pip install -e .
superset db upgrade
superset fab create-admin --username admin --firstname Admin --lastname User --email admin@superset.local --password admin
superset init
superset load-examples
superset run -p 8091 --with-threads --reload --debugger --debug
```

Install and start the frontend in a second terminal:

```bash
cd superset-frontend
npm install
npm run dev-server -- --port=8092 --env=--supersetPort=8091
```

Start the AI agent in a third terminal from the repository root:

```bash
source venv/bin/activate
uvicorn superset_ai_agent.app:app --reload --env-file .env.ai-agent --port 8097
```

Open the frontend:

```text
http://localhost:8092
```

## Model Providers

The backend chooses a model provider with `AI_AGENT_MODEL_PROVIDER`. Set these
values in `docker/.env-ai-agent` for Docker smoke tests or `.env.ai-agent` for
native development.

| Provider | Status | Local test coverage |
| --- | --- | --- |
| `ollama` | Working local path | Real local Ollama + unit tests |
| `openai` | Implemented | Mocked locally; deploy smoke required |
| `openai_compatible` | Implemented | Mocked locally; deploy smoke required |
| `azure_openai` | Implemented | Mocked locally; deploy smoke required |

Ollama:

```env
AI_AGENT_MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
AI_AGENT_MODEL=qwen2.5-coder:7b
```

OpenAI:

```env
AI_AGENT_MODEL_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

OpenAI-compatible gateway:

```env
AI_AGENT_MODEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-gateway.example.com/v1
OPENAI_COMPATIBLE_API_KEY=your_key
OPENAI_COMPATIBLE_MODEL=your_model
OPENAI_COMPATIBLE_REQUIRE_API_KEY=true
OPENAI_COMPATIBLE_STRUCTURED_OUTPUT=json_schema
```

The OpenAI-compatible client uses direct HTTP calls to `/chat/completions` with
minimal headers. If a gateway rejects JSON-schema structured output with a
client-side validation error, it falls back to JSON-object mode and then
prompt-only JSON instructions.

Azure OpenAI:

```env
AI_AGENT_MODEL_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_KEY=your_key
AZURE_OPENAI_MODEL=your-deployment-name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_STRUCTURED_OUTPUT=json_schema
```

The Azure OpenAI client calls the deployment-specific chat completions endpoint
using the `api-key` header. `AZURE_OPENAI_MODEL` is the Azure deployment name.
If a deployment rejects JSON-schema structured output, the client falls back to
JSON-object mode and then prompt-only JSON instructions.

## SQL Lab Conversation Panel

The Superset frontend includes a Copilot-style SQL Lab right-sidebar chat panel
that calls this standalone service. In native development, run the frontend on
`8092` and the AI agent on `8097` as shown above. The frontend proxies
`/ai-agent` to the native AI agent by default.

The panel keeps a conversation transcript, tracks the active SQL Lab database
and schema as context, and treats generated SQL as an artifact. SQL artifacts
can be inserted into the active editor, copied, validated, or executed through
an explicit follow-up turn.

The composer exposes an execution-mode selector:

- `manual`: generated SQL is returned for user approval and is not executed.
- `read_only`: validated read-only SQL can be executed by the agent.
- `auto`: the agent may automatically execute validated read-only SQL. The POC
  does not execute DDL or DML in this mode.

Every conversation SQL draft is parsed with `sqlglot`, limited to a single
SELECT/CTE-style statement, checked for destructive keywords and expressions,
and normalized with a conservative `LIMIT` before execution. The LangGraph
conversation workflow can take multiple SQL tool turns in one run: draft SQL,
validate it, execute it when the selected mode permits, feed the rows back to
the model, and stop when it can answer. The loop is capped by
`AI_AGENT_MAX_SQL_ITERATIONS`.

Conversation state is process-local by default. These values can be edited in
`.env.ai-agent` or `docker/.env-ai-agent`:

```env
AI_AGENT_CONVERSATION_STORE=memory
AI_AGENT_MAX_HISTORY_MESSAGES=12
AI_AGENT_MAX_PROMPT_RESULT_ROWS=5
AI_AGENT_MAX_SQL_ITERATIONS=3
```

The in-memory transcript store is intended for local development and smoke
tests. Durable conversation memory, summarization, retrieval, and user-scoped
persistence are deferred. The boundary is `ConversationStore` in
`superset_ai_agent/conversations/store.py`; a persistent store can implement
that protocol without changing the graph or UI API.

## Smoke Test

```bash
curl http://localhost:8097/health
curl http://localhost:8097/models
```

Validate SQL without calling the model:

```bash
curl -s http://localhost:8097/agent/validate-sql \
  -H 'content-type: application/json' \
  -d '{"sql": "select * from birth_names", "dialect": "sqlite"}'
```

Generate SQL:

```bash
curl -s http://localhost:8097/agent/query \
  -H 'content-type: application/json' \
  -d '{
    "question": "Show the top 10 names by total births",
    "database_id": 1,
    "dataset_ids": [16],
    "execute": false
  }'
```

Execution is off by default. Keep it off while testing prompt quality.

Start a conversation:

```bash
curl -s http://localhost:8097/agent/conversations \
  -H 'content-type: application/json' \
  -d '{
    "scope": {
      "database_id": 1,
      "schema_name": null,
      "dataset_ids": [16]
    }
  }'
```

Send a conversation turn:

```bash
curl -s http://localhost:8097/agent/conversations/<conversation-id>/messages \
  -H 'content-type: application/json' \
  -d '{
    "message": "Show the top 10 names by total births",
    "scope": {
      "database_id": 1,
      "schema_name": null,
      "dataset_ids": [16]
    },
    "execution_mode": "manual"
  }'
```

## POC Limitations

- The `local` Superset adapter is for development and imports Superset in the
  agent process.
- The `rest` and `mcp` Superset adapters are implemented with mocked local
  transport tests; validate them against real Superset services in Docker.
- OpenAI, OpenAI-compatible, and Azure OpenAI providers are mocked locally;
  validate them in the deployment environment with real credentials and gateway
  URLs.
- Conversation transcripts use an in-memory development store by default.
- Durable conversation memory, RAG, skills, eval suite, persistent conversation
  store, or user identity propagation are not implemented yet.
- SQL validation is a conservative POC guard, not a complete security boundary.
- `auto` execution is intentionally limited to validated read-only SQL.

## Future Extension Points

- Add persistent conversation stores, starting with SQLite or Redis.
- Add durable conversation memory through summarization and retrieval while
  keeping `ConversationStore` as the persistence boundary.
- Harden the `rest` and `mcp` adapters against the target deployment auth
  setup.
- Add more provider clients or route through LiteLLM/Bedrock as needed.
- Replace file prompts with a versioned prompt registry.
- Add RAG through `context/rag_stub.py`.
- Add skills/playbooks as another context provider.
