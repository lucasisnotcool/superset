# Superset AI Agent POC

Standalone Python API for a lightweight text-to-SQL agent using LangGraph and a
pluggable model provider.

This proof of concept is intentionally separate from Superset core. It exposes
a small API that can be called by a Superset extension, test client, or any
other agent UI.

## Architecture

```text
Client / Superset extension
  -> FastAPI service
  -> LangGraph text-to-SQL workflow
  -> Ollama / OpenAI / OpenAI-compatible / Azure OpenAI model
  -> SupersetClient adapter
  -> Superset metadata and governed SQL execution
```

The Superset integration boundary is documented in
`integrations/superset/README.md`.

## Install

From the repository root:

```bash
source venv/bin/activate
python -m pip install -r requirements-ai-agent.txt
```

Optional environment configuration:

```bash
cp superset_ai_agent/.env.example .env.ai-agent
```

## Run

Start Superset on port 8088 first. For local-only development, Ollama can run
on port 11434.

```bash
export AI_AGENT_MODEL=qwen2.5-coder:7b
export SUPERSET_AGENT_ADAPTER=local
uvicorn superset_ai_agent.app:app --reload --port 5050
```

Or use the repository helper:

```bash
make ai-agent
```

## Docker Smoke

Docker smoke tests should use OpenAI or an OpenAI-compatible provider. Do not
use Ollama in the Docker deploy path.

Create the agent env file and fill in model credentials:

```bash
cp docker/.env-ai-agent.example docker/.env-ai-agent
```

PowerShell:

```powershell
Copy-Item docker/.env-ai-agent.example docker/.env-ai-agent
notepad docker/.env-ai-agent
```

Start Superset, the frontend dev server, nginx, and the standalone agent:

```bash
make up-ai
```

PowerShell:

```powershell
.\scripts\docker-compose-ai-up.ps1
```

For detached mode:

```bash
make up-ai-detached
```

PowerShell:

```powershell
.\scripts\docker-compose-ai-up.ps1 -Detached
```

The `make` targets use `scripts/docker-compose-ai-up.sh`, which assigns free
host ports and validates `docker/.env-ai-agent` before startup. PowerShell can
use `scripts/docker-compose-ai-up.ps1`, which mirrors the same Docker smoke
workflow with PowerShell-style flags. On ARM64 Docker engines these helpers also
apply the Superset Python compatibility override needed for the current
`cryptography` wheel. x86 Linux and Windows Docker engines keep the normal
pinned dependency set unless `SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION` is set
explicitly.

PowerShell helper commands:

```powershell
.\scripts\docker-compose-ai-up.ps1 dry-run
.\scripts\docker-compose-ai-up.ps1 ports
.\scripts\docker-compose-ai-up.ps1 ps
.\scripts\docker-compose-ai-up.ps1 logs -Follow -Service superset-ai-agent
.\scripts\docker-compose-ai-up.ps1 down
```

If Windows blocks script execution, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-compose-ai-up.ps1 -Detached
```

Smoke-test the service:

```bash
curl http://localhost:5051/health
curl http://localhost:9000/ai-agent/health
```

PowerShell:

```powershell
curl.exe http://localhost:5051/health
curl.exe http://localhost:9000/ai-agent/health
```

## Windows PowerShell Fresh Setup

Run these commands from the repository root. Use
`scripts/docker-compose-ai-up.ps1` for native PowerShell Docker smoke tests, or
use `scripts/docker-compose-ai-up.sh` from WSL2/Git Bash.

### Docker Smoke From A Fresh Clone

Docker must use OpenAI or an OpenAI-compatible API. Do not configure Ollama in
`docker/.env-ai-agent`.

```powershell
git clone <repo-url>
cd superset
Copy-Item docker/.env-ai-agent.example docker/.env-ai-agent
notepad docker/.env-ai-agent
```

For an OpenAI-compatible gateway, set:

```env
AI_AGENT_MODEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-gateway.example.com/v1
OPENAI_COMPATIBLE_API_KEY=your_key
OPENAI_COMPATIBLE_MODEL=your_model
OPENAI_COMPATIBLE_REQUIRE_API_KEY=true
OPENAI_COMPATIBLE_STRUCTURED_OUTPUT=json_schema
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

Start Docker from PowerShell:

```powershell
.\scripts\docker-compose-ai-up.ps1 -Detached
.\scripts\docker-compose-ai-up.ps1 ps
```

Or start Docker from WSL2/Git Bash:

```bash
./scripts/docker-compose-ai-up.sh -d
./scripts/docker-compose-ai-up.sh ps
```

The script prints the actual URLs. If a default port is busy, it chooses the
next available port.

Common endpoints:

```text
Superset: http://localhost:8088
Frontend dev server / proxy: http://localhost:9000
AI Agent: http://localhost:5050 or http://localhost:5051
AI Proxy: http://localhost:9000/ai-agent
```

Smoke-test from PowerShell:

```powershell
curl.exe http://localhost:9000/ai-agent/health
curl.exe http://localhost:5050/health
```

If the script assigned `5051`, use:

```powershell
curl.exe http://localhost:5051/health
```

Follow logs from PowerShell:

```powershell
.\scripts\docker-compose-ai-up.ps1 logs -Follow -Service superset-ai-agent
```

Or follow logs from WSL2/Git Bash:

```bash
./scripts/docker-compose-ai-up.sh logs -f superset-ai-agent
```

Stop the stack from PowerShell:

```powershell
.\scripts\docker-compose-ai-up.ps1 down
```

Or stop the stack from WSL2/Git Bash:

```bash
./scripts/docker-compose-ai-up.sh down
```

### Native Dev From A Fresh Clone

Use native dev when changing the AI agent or frontend. Ollama is supported only
for local native development.

```powershell
git clone <repo-url>
cd superset
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-ai-agent.txt
```

If you are running the Superset backend natively too:

```powershell
python -m pip install -r requirements/development.txt
python -m pip install -e .
superset db upgrade
superset fab create-admin --username admin --firstname Admin --lastname User --email admin@superset.local --password admin
superset init
superset load-examples
superset run -p 8088 --with-threads --reload --debugger --debug
```

If native Superset dependencies fail on Windows, run Superset with Docker and
run only the AI agent natively.

Install and start the frontend in a second PowerShell window:

```powershell
cd superset-frontend
npm install
$env:SUPERSET_AI_AGENT_PROXY="http://127.0.0.1:5050"
npm run dev-server
```

Start the AI agent with Ollama in a third PowerShell window:

```powershell
.\venv\Scripts\Activate.ps1
$env:AI_AGENT_MODEL_PROVIDER="ollama"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:AI_AGENT_MODEL="qwen2.5-coder:7b"
$env:SUPERSET_AGENT_ADAPTER="local"
uvicorn superset_ai_agent.app:app --reload --port 5050
```

Or start the AI agent with an OpenAI-compatible API:

```powershell
.\venv\Scripts\Activate.ps1
$env:AI_AGENT_MODEL_PROVIDER="openai_compatible"
$env:OPENAI_COMPATIBLE_BASE_URL="https://your-gateway.example.com/v1"
$env:OPENAI_COMPATIBLE_API_KEY="your_key"
$env:OPENAI_COMPATIBLE_MODEL="your_model"
uvicorn superset_ai_agent.app:app --reload --port 5050
```

Or start the AI agent with Azure OpenAI:

```powershell
.\venv\Scripts\Activate.ps1
$env:AI_AGENT_MODEL_PROVIDER="azure_openai"
$env:AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
$env:AZURE_OPENAI_KEY="your_key"
$env:AZURE_OPENAI_MODEL="your-deployment-name"
$env:AZURE_OPENAI_API_VERSION="2024-02-15-preview"
uvicorn superset_ai_agent.app:app --reload --port 5050
```

Open the frontend:

```text
http://localhost:9000
```

## Model Providers

The backend chooses a model provider with `AI_AGENT_MODEL_PROVIDER`.

| Provider | Status | Local test coverage |
| --- | --- | --- |
| `ollama` | Working local path | Real local Ollama + unit tests |
| `openai` | Implemented | Mocked locally; deploy smoke required |
| `openai_compatible` | Implemented | Mocked locally; deploy smoke required |
| `azure_openai` | Implemented | Mocked locally; deploy smoke required |

Ollama:

```bash
export AI_AGENT_MODEL_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434
export AI_AGENT_MODEL=qwen2.5-coder:7b
```

OpenAI:

```bash
export AI_AGENT_MODEL_PROVIDER=openai
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1-mini
export OPENAI_BASE_URL=https://api.openai.com/v1
```

OpenAI-compatible gateway:

```bash
export AI_AGENT_MODEL_PROVIDER=openai_compatible
export OPENAI_COMPATIBLE_BASE_URL=https://your-gateway.example.com/v1
export OPENAI_COMPATIBLE_API_KEY=...
export OPENAI_COMPATIBLE_MODEL=...
export OPENAI_COMPATIBLE_STRUCTURED_OUTPUT=json_schema
```

For no-auth local gateways, set:

```bash
export OPENAI_COMPATIBLE_REQUIRE_API_KEY=false
```

The OpenAI-compatible client uses direct HTTP calls to `/chat/completions` with
minimal headers. If a gateway rejects JSON-schema structured output with a
client-side validation error, it falls back to JSON-object mode and then
prompt-only JSON instructions.

Azure OpenAI:

```bash
export AI_AGENT_MODEL_PROVIDER=azure_openai
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
export AZURE_OPENAI_KEY=...
export AZURE_OPENAI_MODEL=your-deployment-name
export AZURE_OPENAI_API_VERSION=2024-02-15-preview
export AZURE_OPENAI_STRUCTURED_OUTPUT=json_schema
```

The Azure OpenAI client calls the deployment-specific chat completions endpoint
using the `api-key` header. `AZURE_OPENAI_MODEL` is the Azure deployment name.
If a deployment rejects JSON-schema structured output, the client falls back to
JSON-object mode and then prompt-only JSON instructions.

## SQL Lab Panel

The Superset frontend includes a minimal SQL Lab right-sidebar panel that calls
this standalone service. Start the frontend dev server with:

```bash
cd superset-frontend
SUPERSET_AI_AGENT_PROXY=http://127.0.0.1:5050 npm run dev-server
```

The panel inserts generated SQL into the active SQL Lab editor. Query execution
should stay in SQL Lab unless the agent request explicitly enables execution.

## Smoke Test

```bash
curl http://localhost:5050/health
curl http://localhost:5050/models
```

Validate SQL without calling the model:

```bash
curl -s http://localhost:5050/agent/validate-sql \
  -H 'content-type: application/json' \
  -d '{"sql": "select * from birth_names", "dialect": "sqlite"}'
```

Generate SQL:

```bash
curl -s http://localhost:5050/agent/query \
  -H 'content-type: application/json' \
  -d '{
    "question": "Show the top 10 names by total births",
    "database_id": 1,
    "dataset_ids": [16],
    "execute": false
  }'
```

Execution is off by default. Keep it off while testing prompt quality.

## POC Limitations

- The `local` Superset adapter is for development and imports Superset in the
  agent process.
- The `rest` and `mcp` Superset adapters are implemented with mocked local
  transport tests; validate them against real Superset services in Docker.
- OpenAI, OpenAI-compatible, and Azure OpenAI providers are mocked locally;
  validate them in the deployment environment with real credentials and gateway
  URLs.
- No RAG, skills, eval suite, or user identity propagation is implemented yet.
- SQL validation is a conservative POC guard, not a complete security boundary.
- Review generated SQL before running it.

## Future Seams

- Harden the `rest` and `mcp` adapters against the target deployment auth
  setup.
- Add more provider clients or route through LiteLLM/Bedrock as needed.
- Replace file prompts with a versioned prompt registry.
- Add RAG through `context/rag_stub.py`.
- Add skills/playbooks as another context provider.
