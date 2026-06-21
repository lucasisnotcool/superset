# Superset Integration Adapter

This directory is the handoff boundary between the standalone AI agent backend
and Apache Superset.

The agent graph calls only the `SupersetClient` protocol in `client.py`. It
does not import Superset models, know API paths, manage CSRF tokens, or handle
MCP transport details directly.

## Adapter Modes

```bash
SUPERSET_AGENT_ADAPTER=local|rest|mcp
SUPERSET_BASE_URL=http://localhost:8091
SUPERSET_MCP_URL=http://localhost:8098/mcp
```

| Mode | Class | Purpose |
| --- | --- | --- |
| `local` | `LocalSupersetClient` | Development adapter that imports Superset in process. |
| `rest` | `SupersetRestClient` | Production-shaped authenticated Superset REST adapter. |
| `mcp` | `SupersetMcpClient` | Production-shaped Superset MCP JSON-RPC tool adapter. |

`create_superset_client(config)` selects the adapter. The graph construction
and graph nodes stay unchanged across modes.

## High-Level Contract

These methods are stable for agent builders:

| Method | Purpose | Normalized return |
| --- | --- | --- |
| `list_databases()` | List databases visible to the integration identity. | `list[DatabaseSummary]` |
| `list_datasets(database_id, dataset_ids, limit)` | List dataset metadata for prompt context. | `list[DatasetMetadata]` |
| `get_agent_context(database_id, dataset_ids)` | Build compact database + dataset context. | `AgentContext` |
| `get_database_dialect(database_id)` | Return backend/dialect hints for validation and prompting. | `str | None` |
| `execute_sql(database_id, sql, schema_name, limit)` | Execute validated SQL and cap rows. | `ExecutionResult` |

The graph should use only these high-level methods.

## Low-Level Controls

Adapters also expose raw controls for engineers building custom agents.

### REST Low-Level Methods

| Method | Description |
| --- | --- |
| `request(method, path, params=None, json=None, headers=None)` | Authenticated REST request. Adds bearer and CSRF headers when needed and keeps Superset session cookies in the adapter HTTP client. |
| `list_databases_raw(page_size=100)` | Raw `GET /api/v1/database/`. |
| `get_database_raw(database_id)` | Raw `GET /api/v1/database/{id}`. |
| `list_datasets_raw(database_id, limit)` | Raw `GET /api/v1/dataset/`. |
| `get_dataset_raw(dataset_id)` | Raw `GET /api/v1/dataset/{id}`. |
| `execute_sql_raw(database_id, sql, schema_name, limit)` | Raw `POST /api/v1/sqllab/execute/`. |
| `get_sqllab_results_raw(key)` | Poll raw `GET /api/v1/sqllab/results/`. |

### MCP Low-Level Methods

| Method | Description |
| --- | --- |
| `call_json_rpc(method, params=None)` | Raw HTTP JSON-RPC request. |
| `call_tool(name, arguments=None)` | Raw MCP tool call with content unwrapping. |
| `list_tools()` | Raw MCP tool discovery. |
| `get_tool_schema(name)` | Return a tool input schema if exposed. |
| `read_resource(uri)` | Raw MCP resource read. |

## REST Auth

The REST adapter supports three deploy patterns:

1. Pre-issued bearer token:

```bash
SUPERSET_AGENT_ADAPTER=rest
SUPERSET_BASE_URL=http://superset:8088
SUPERSET_AUTH_TOKEN=...
```

2. Username/password login through Superset security API:

```bash
SUPERSET_AGENT_ADAPTER=rest
SUPERSET_BASE_URL=http://superset:8088
SUPERSET_USERNAME=agent-service-account
SUPERSET_PASSWORD=...
SUPERSET_AUTH_PROVIDER=db
```

3. Explicit CSRF token for controlled environments:

```bash
SUPERSET_CSRF_TOKEN=...
```

For normal REST use, leave `SUPERSET_CSRF_TOKEN` empty. Superset binds CSRF to
the Flask session, so the adapter fetches a CSRF token itself and reuses the
same HTTP client cookie jar for the subsequent mutating request. A copied CSRF
token without the matching session cookie can still fail with a missing CSRF
session token.

If no token or username is configured, `request()` sends no `Authorization`
header. That supports deployments where an upstream sidecar or service mesh
injects identity.

## REST Endpoint Payloads

### List Databases

```http
GET {SUPERSET_BASE_URL}/api/v1/database/?q=(page:0,page_size:100,order_column:database_name,order_direction:asc)
Accept: application/json
Authorization: Bearer <token>
```

Normalized item:

```json
{
  "id": 1,
  "name": "examples",
  "backend": "sqlite"
}
```

### Get Database

```http
GET {SUPERSET_BASE_URL}/api/v1/database/{database_id}
Accept: application/json
Authorization: Bearer <token>
```

`result.backend` feeds `get_database_dialect()`.

### List Datasets

For database-wide context:

```http
GET {SUPERSET_BASE_URL}/api/v1/dataset/?q=(page:0,page_size:8,order_column:table_name,order_direction:asc,filters:!((col:database,opr:rel_o_m,value:1)))
Accept: application/json
Authorization: Bearer <token>
```

For explicit dataset IDs:

```http
GET {SUPERSET_BASE_URL}/api/v1/dataset/{dataset_id}
Accept: application/json
Authorization: Bearer <token>
```

Normalized dataset:

```json
{
  "id": 16,
  "table_name": "birth_names",
  "schema_name": null,
  "database_id": 1,
  "description": null,
  "columns": [
    {
      "name": "name",
      "type": "VARCHAR",
      "is_dttm": false,
      "description": null
    }
  ],
  "metrics": [
    {
      "name": "count",
      "expression": "COUNT(*)",
      "description": null
    }
  ]
}
```

### Execute SQL

```http
POST {SUPERSET_BASE_URL}/api/v1/sqllab/execute/
Content-Type: application/json
Accept: application/json
Authorization: Bearer <token>
X-CSRFToken: <csrf-token>
```

Request body:

```json
{
  "database_id": 1,
  "sql": "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name LIMIT 1000",
  "catalog": null,
  "schema": null,
  "queryLimit": 1000,
  "runAsync": false,
  "expand_data": true
}
```

Normalized result:

```json
{
  "columns": ["name", "total_births"],
  "rows": [
    {
      "name": "Emma",
      "total_births": 123456
    }
  ],
  "row_count": 1
}
```

If SQL Lab returns a results key, the REST adapter polls
`GET /api/v1/sqllab/results/?q=(key:<result-key>)`. Polling is owned by
`rest.py`, not by the graph.

## MCP Auth

```bash
SUPERSET_AGENT_ADAPTER=mcp
SUPERSET_MCP_URL=http://superset-mcp:8098/mcp
SUPERSET_MCP_AUTH_TOKEN=...
```

If `SUPERSET_MCP_AUTH_TOKEN` is not set, the adapter falls back to
`SUPERSET_AUTH_TOKEN`. If neither is set, requests are sent without
authorization for environments that inject identity upstream.

## MCP Tool Payloads

MCP calls use HTTP JSON-RPC:

```json
{
  "jsonrpc": "2.0",
  "id": "agent-1",
  "method": "tools/call",
  "params": {
    "name": "list_databases",
    "arguments": {
      "request": {
        "page": 1,
        "page_size": 100,
        "order_column": "database_name",
        "order_direction": "asc",
        "select_columns": ["id", "database_name", "backend"]
      }
    }
  }
}
```

### `get_database_info`

```json
{
  "jsonrpc": "2.0",
  "id": "agent-2",
  "method": "tools/call",
  "params": {
    "name": "get_database_info",
    "arguments": {
      "request": {
        "identifier": 1
      }
    }
  }
}
```

### `list_datasets`

The high-level MCP adapter first resolves the database name from
`get_database_info`, then filters datasets by `database_name`:

```json
{
  "jsonrpc": "2.0",
  "id": "agent-3",
  "method": "tools/call",
  "params": {
    "name": "list_datasets",
    "arguments": {
      "request": {
        "page": 1,
        "page_size": 8,
        "order_column": "table_name",
        "order_direction": "asc",
        "select_columns": [
          "id",
          "table_name",
          "schema",
          "database_id",
          "description"
        ],
        "filters": [
          {
            "col": "database_name",
            "opr": "eq",
            "value": "examples"
          }
        ]
      }
    }
  }
}
```

For explicit dataset IDs, use `get_dataset_info`:

```json
{
  "jsonrpc": "2.0",
  "id": "agent-4",
  "method": "tools/call",
  "params": {
    "name": "get_dataset_info",
    "arguments": {
      "request": {
        "identifier": 16,
        "select_columns": [
          "id",
          "table_name",
          "schema",
          "database_id",
          "description",
          "columns",
          "metrics"
        ],
        "column_fields": ["column_name", "type", "is_dttm", "description"]
      }
    }
  }
}
```

### `execute_sql`

```json
{
  "jsonrpc": "2.0",
  "id": "agent-5",
  "method": "tools/call",
  "params": {
    "name": "execute_sql",
    "arguments": {
      "request": {
        "database_id": 1,
        "sql": "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name LIMIT 1000",
        "schema": null,
        "limit": 1000,
        "timeout": 30,
        "dry_run": false,
        "force_refresh": false
      }
    }
  }
}
```

Normalized MCP SQL result:

```json
{
  "columns": ["name", "total_births"],
  "rows": [
    {
      "name": "Emma",
      "total_births": 123456
    }
  ],
  "row_count": 1
}
```

## Error Ownership

Adapters raise `SupersetAdapterError` for transport errors, auth failures,
permission failures, malformed payloads, and SQL execution failures. The graph
should not inspect REST/MCP transport details.

## Deployment Guidance

Local development can use:

```bash
SUPERSET_AGENT_ADAPTER=local
AI_AGENT_MODEL_PROVIDER=ollama
```

Deploy environments should avoid Ollama and use:

```bash
SUPERSET_AGENT_ADAPTER=rest
AI_AGENT_MODEL_PROVIDER=openai_compatible
```

or:

```bash
SUPERSET_AGENT_ADAPTER=mcp
AI_AGENT_MODEL_PROVIDER=openai
```

Azure OpenAI deployments can use either Superset adapter mode:

```bash
SUPERSET_AGENT_ADAPTER=rest
AI_AGENT_MODEL_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_KEY=...
AZURE_OPENAI_MODEL=your-deployment-name
AZURE_OPENAI_API_VERSION=2024-02-15-preview
```

`AZURE_OPENAI_MODEL` is the Azure deployment name. This model-provider setting
does not change the Superset adapter contract; REST and MCP payloads remain the
same.
