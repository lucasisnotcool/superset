# Superset AI Agent POC

Standalone Python API for lightweight conversational database assistance and
text-to-SQL using LangGraph and a pluggable model provider.

This proof of concept is intentionally separate from Superset core. It exposes
a small API that can be called by a Superset extension, test client, or any
other agent UI.

This README contains Windows PowerShell instructions. For macOS or other Bash
workflows, use [MACOS.md](MACOS.md).

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

## Windows PowerShell Fresh Setup

Run these commands from the repository root.

### Prerequisites

Install these on the Windows host before running the Docker or native setup
commands. They are host tools; `npm install`, Python `pip`, and Docker builds
do not install them for you.

| Dependency | Needed for | Check |
| --- | --- | --- |
| Rancher Desktop with Docker-compatible CLI | Docker smoke stack and Compose helpers | `docker version` and `docker compose version` |
| PowerShell | Running the documented Windows commands | `$PSVersionTable.PSVersion` |
| Python 3.11 | Native AI-agent development and local tests | `py -3.11 --version` |
| Node.js and npm | Native frontend development | `node --version` and `npm --version` |
| Git | Cloning and working with this repository | `git --version` |

For Rancher Desktop, use a configuration that provides the `docker` CLI and
Docker Compose v2. The helper scripts expect `docker compose ...` to work from
PowerShell.

Docker smoke tests also require real model-provider access. Use OpenAI,
Azure OpenAI, or an OpenAI-compatible gateway in `superset_ai_agent/.env`.
Ollama is supported only for native agent development.

### Docker Smoke

Docker smoke tests should use OpenAI, Azure OpenAI, or an OpenAI-compatible
provider. Ollama is only supported for native agent development.

Create the shared agent env file and edit it:

```powershell
Copy-Item superset_ai_agent/.env.example superset_ai_agent/.env
notepad superset_ai_agent/.env
```

For an OpenAI-compatible gateway, set these values in
`superset_ai_agent/.env`:

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

```powershell
.\scripts\docker-compose-ai-up.ps1 -Detached
.\scripts\docker-compose-ai-up.ps1 ps
```

The helper validates `superset_ai_agent/.env`, finds a single host port
starting at `8090` when available, and prints the actual site URL. The default
Docker smoke URL is:

```text
Site: http://localhost:8090
AI proxy: http://localhost:8090/ai-agent
```

All other Docker services are reachable only inside the Compose network. If
`8090` is busy, the helper prints the selected replacement port.

On Windows, the PowerShell helper also includes `docker-compose.no-bind.yml`.
That overlay avoids host bind mounts, which can be misparsed by some Windows
Docker/Rancher setups. The Docker smoke stack runs from the code packaged into
the images. A Docker-managed `superset_static_assets` volume shares the webpack
manifest from `superset-node` to the Superset Flask container, so the page can
render the SPA script tags without host mounts. Rebuild the stack after changing
Superset, frontend, Docker config, or AI agent source.

On ARM64 Docker engines the helper also applies the Superset Python
compatibility override needed for the pinned dependency set. x86 Linux and
Windows Docker engines keep the normal pinned dependency set unless
`SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION` is set outside the helper.

Smoke-test the service:

```powershell
curl.exe http://localhost:8090/health
curl.exe http://localhost:8090/ai-agent/health
```

If the script printed another site port, use that port:

```powershell
curl.exe http://localhost:<NGINX_HOST_PORT>/health
curl.exe http://localhost:<NGINX_HOST_PORT>/ai-agent/health
```

PowerShell helper commands:

```powershell
.\scripts\docker-compose-ai-up.ps1 dry-run
.\scripts\docker-compose-ai-up.ps1 ports
.\scripts\docker-compose-ai-up.ps1 ps
.\scripts\docker-compose-ai-up.ps1 logs -Follow -Service superset-ai-agent
.\scripts\docker-compose-ai-up.ps1 restart -Service superset
.\scripts\docker-compose-ai-up.ps1 down
```

If Windows blocks script execution, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-compose-ai-up.ps1 -Detached
```

### Native Dev

Use native dev when changing the AI agent or frontend. Ollama is supported only
for local native development.

Install Python dependencies:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-ai-agent.txt
```

Create the native agent env file and edit it:

```powershell
Copy-Item superset_ai_agent/.env.example superset_ai_agent/.env
notepad superset_ai_agent/.env
```

Native dev uses local process ports and is separate from Docker's single
published port. The shared env file defaults to the REST adapter so native and
Docker exercise the same Superset boundary:

```env
SUPERSET_AGENT_ADAPTER=rest
SUPERSET_BASE_URL=http://localhost:8091
SUPERSET_USERNAME=admin
SUPERSET_PASSWORD=admin
AI_AGENT_CORS_ALLOWED_ORIGINS=http://localhost:8090,http://127.0.0.1:8090,http://localhost:8092,http://127.0.0.1:8092
```

If you are running the Superset backend natively too:

```powershell
python -m pip install -r requirements/development.txt
python -m pip install -e .
superset db upgrade
superset fab create-admin --username admin --firstname Admin --lastname User --email admin@superset.local --password admin
superset init
superset load-examples
superset run -p 8091 --with-threads --reload --debugger --debug
```

If native Superset dependencies fail on Windows, run Superset with Docker and
run only the AI agent natively.

Install and start the frontend in a second PowerShell window:

```powershell
cd superset-frontend
npm install
npm run dev-server
```

Start the AI agent in a third PowerShell window from the repository root:

```powershell
.\venv\Scripts\Activate.ps1
uvicorn superset_ai_agent.app:app --reload --env-file superset_ai_agent/.env --port 8097
```

Open the frontend:

```text
http://localhost:8092
```

## Model Providers

The backend chooses a model provider with `AI_AGENT_MODEL_PROVIDER`. Set these
values in `superset_ai_agent/.env`.

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
that calls this standalone service. In Docker, open `http://localhost:8090`;
nginx proxies `/ai-agent` to the agent container. In native development, run
the frontend on `8092` and the AI agent on `8097` as shown above. The frontend
proxies `/ai-agent` to the native AI agent by default.

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
`superset_ai_agent/.env`:

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

## Native API Smoke Test

For Docker, use the `/ai-agent` route on the site URL printed by the helper.
Use these direct API calls only when running the AI agent natively.

```powershell
curl.exe http://localhost:8097/health
curl.exe http://localhost:8097/models
```

Validate SQL without calling the model:

```powershell
Invoke-RestMethod -Uri http://localhost:8097/agent/validate-sql -Method Post -ContentType "application/json" -Body '{"sql":"select * from birth_names","dialect":"sqlite"}'
```

Generate SQL:

```powershell
Invoke-RestMethod -Uri http://localhost:8097/agent/query -Method Post -ContentType "application/json" -Body '{"question":"Show the top 10 names by total births","database_id":1,"dataset_ids":[16],"execute":false}'
```

Execution is off by default. Keep it off while testing prompt quality.

Start a conversation:

```powershell
Invoke-RestMethod -Uri http://localhost:8097/agent/conversations -Method Post -ContentType "application/json" -Body '{"scope":{"database_id":1,"schema_name":null,"dataset_ids":[16]}}'
```

Send a conversation turn:

```powershell
Invoke-RestMethod -Uri http://localhost:8097/agent/conversations/<conversation-id>/messages -Method Post -ContentType "application/json" -Body '{"message":"Show the top 10 names by total births","scope":{"database_id":1,"schema_name":null,"dataset_ids":[16]},"execution_mode":"manual"}'
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
