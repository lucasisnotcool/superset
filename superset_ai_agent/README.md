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
  -> Ollama / OpenAI / OpenAI-compatible model
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

Start Superset, the frontend dev server, nginx, and the standalone agent:

```bash
make up-ai
```

For detached mode:

```bash
make up-ai-detached
```

The `make` targets use `scripts/docker-compose-ai-up.sh`, which assigns free
host ports and validates `docker/.env-ai-agent` before startup. On ARM64 Docker
engines it also applies the Superset Python compatibility override needed for
the current `cryptography` wheel. x86 Linux and Windows Docker engines keep the
normal pinned dependency set unless `SUPERSET_DOCKER_CRYPTOGRAPHY_VERSION` is
set explicitly.

Smoke-test the service:

```bash
curl http://localhost:5051/health
curl http://localhost:9000/ai-agent/health
```

## Model Providers

The backend chooses a model provider with `AI_AGENT_MODEL_PROVIDER`.

| Provider | Status | Local test coverage |
| --- | --- | --- |
| `ollama` | Working local path | Real local Ollama + unit tests |
| `openai` | Implemented | Mocked locally; deploy smoke required |
| `openai_compatible` | Implemented | Mocked locally; deploy smoke required |

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
- OpenAI and OpenAI-compatible providers are mocked locally; validate them in
  the deployment environment with real credentials and gateway URLs.
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
