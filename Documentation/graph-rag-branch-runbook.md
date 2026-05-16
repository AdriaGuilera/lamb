# Graph RAG Branch Runbook

This note documents what is new or required in `feature/kg-rag-llm-pipeline` to run the Graph RAG / KG-RAG feature locally.

The feature is optional and disabled by default. Running the normal LAMB stack is not enough: the KB service must start with KG-RAG enabled, and Neo4j must be running through the `kg-rag` Docker Compose profile.

## What Is New

- The KB server can augment normal Chroma vector retrieval with LLM-extracted graph entities and relationships.
- Neo4j is required for graph storage and graph expansion.
- The KB server exposes new graph traceability endpoints under `/graph`.
- The KB server exposes benchmark endpoints under `/benchmarks`.
- `KG_RAG_ENABLED=true` is a global feature flag: it exposes Graph RAG controls, but individual knowledge bases opt in with their own `graph_enabled` flag.
- New knowledge bases can be created as graph-enabled. Existing knowledge bases can be migrated so their current Chroma chunks are processed into Neo4j.
- The Svelte knowledge-base detail view includes Graph and Benchmarks UI tabs only for graph-enabled knowledge bases.
- The `kg_rag_query` plugin can be selected for Graph RAG queries while `simple_query` remains the baseline fallback.

## Required Containers

For the full local web interface, run these services from `docker-compose-example.yaml`:

- `openwebui-build`: one-shot Open WebUI frontend build helper.
- `openwebui`: Open WebUI backend/UI on port `8080`.
- `frontend-build`: one-shot Svelte build helper.
- `frontend`: LAMB Svelte dev UI on port `5173`.
- `backend`: LAMB backend API on port `9099`.
- `kb`: LAMB KB server on port `9090`; this is where KG-RAG runs.
- `library-manager`: library service on port `9091`.
- `neo4j`: Graph database on ports `7474` and `7687`; only starts with the `kg-rag` profile.

The local compose file uses these public images:

- `python:3.11-slim`
- `node:20-alpine`
- `neo4j:5-community`

First startup or KB recreation can be slow because the KB container installs Python dependencies at runtime, including large ML packages.

## Required Environment

The root `.env.example` is minimal and may not include every branch variable. For this branch, make sure the root `.env` has the KG-RAG block below, but never commit real secrets.

```env
# Required to expose Graph RAG capabilities in the KB container and UI.
KG_RAG_ENABLED=true

# Ingestion-time graph indexing for knowledge bases that have graph_enabled=true.
KG_RAG_INDEX_ON_INGEST=true

# LLM used by KG-RAG extraction/query logic.
# KG_RAG_OPENAI_API_KEY may fall back to OPENAI_API_KEY and then EMBEDDINGS_APIKEY.
KG_RAG_OPENAI_API_KEY=your-openai-key
KG_RAG_CHAT_MODEL=gpt-4o-mini
KG_RAG_EXTRACTION_MODEL=gpt-5-nano

# Neo4j connection from inside Docker.
KG_RAG_NEO4J_URI=bolt://neo4j:7687
KG_RAG_NEO4J_USER=neo4j
KG_RAG_NEO4J_PASSWORD=change-this-password

# Graph expansion tuning.
KG_RAG_GRAPH_DEPTH=2
KG_RAG_LIMIT_FACTOR=4
KG_RAG_EXTRACTION_MAX_WORKERS=4

# Enables the advanced query plugin registration.
PLUGIN_KG_RAG_QUERY=ADVANCED
```

For local Docker Compose, also ensure these base values exist:

```env
LAMB_PROJECT_PATH=/absolute/path/to/lamb
OWI_BASE_URL=http://openwebui:8080
OWI_PUBLIC_BASE_URL=http://localhost:8080
LAMB_DB_PATH=/absolute/path/to/lamb
LAMB_KB_SERVER=http://kb:9090
```

For this workspace, the successful runtime command used `LAMB_PROJECT_PATH="$PWD"` so the existing `.env` did not need to be edited.

## Start The Stack Locally

From the `lamb` directory:

```bash
LAMB_PROJECT_PATH="$PWD" \
KG_RAG_ENABLED=true \
docker compose -f docker-compose-example.yaml --profile kg-rag up -d
```

If `.env` already has `KG_RAG_ENABLED=true`, the shorter form is enough:

```bash
LAMB_PROJECT_PATH="$PWD" docker compose -f docker-compose-example.yaml --profile kg-rag up -d
```

The `--profile kg-rag` flag is important. Without it, Neo4j is not included.

## Verify It Is Running

Check container state:

```bash
LAMB_PROJECT_PATH="$PWD" \
KG_RAG_ENABLED=true \
docker compose -f docker-compose-example.yaml --profile kg-rag ps
```

Check that the KB container really received the feature flag:

```bash
docker inspect lamb-kb-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(KG_RAG_ENABLED|KG_RAG_NEO4J_URI|PLUGIN_KG_RAG_QUERY)='
```

Expected important values:

```text
KG_RAG_ENABLED=true
KG_RAG_NEO4J_URI=bolt://neo4j:7687
PLUGIN_KG_RAG_QUERY=ADVANCED
```

Check service health:

```bash
curl http://localhost:9099/status
curl http://localhost:9090/health
curl http://localhost:9091/health
```

Expected responses include:

```text
{"status":true}
{"status":"ok","version":"0.1.0"}
{"status":"ok","service":"library-manager"}
```

Check that the branch routes are registered:

```bash
curl -s http://localhost:9090/openapi.json -o /tmp/kb_openapi.json
python3 - <<'PY'
import json
with open("/tmp/kb_openapi.json") as fh:
  data = json.load(fh)
paths = data.get("paths", {})
print("graph paths:", sum(path.startswith("/graph") for path in paths))
print("benchmark paths:", sum(path.startswith("/benchmarks") for path in paths))
PY
```

You should see non-zero counts for both graph and benchmark paths.

Check feature availability through the LAMB backend proxy:

```bash
curl -H "Authorization: Bearer <creator-token>" \
  http://localhost:9099/creator/graph/status
```

The important fields are:

```json
{
  "enabled": true,
  "index_on_ingest": true,
  "neo4j_configured": true,
  "neo4j_available": true
}
```

## Local URLs

- LAMB web interface: `http://localhost:5173/`
- Open WebUI: `http://localhost:8080/`
- Neo4j Browser: `http://localhost:7474/`
- LAMB backend status: `http://localhost:9099/status`
- KB server health: `http://localhost:9090/health`
- Library Manager health: `http://localhost:9091/health`

Default local LAMB login verified for this stack:

```text
Email: admin@owi.com
Password: admin
```

## Neo4j Notes

Inside Docker, the KB service must use:

```text
bolt://neo4j:7687
```

From the host machine, use:

```text
bolt://localhost:7687
```

Neo4j Browser is available at `http://localhost:7474/` with the configured Neo4j username and password from `.env`.

## Common Pitfalls

- If `KG_RAG_ENABLED=false`, the stack can be up but Graph RAG is disabled.
- If `KG_RAG_ENABLED=true` but a KB has `graph_enabled=false`, the Graph and Benchmarks tabs stay hidden for that KB and ingestion skips graph indexing.
- Existing KBs do not get graph data automatically just because the global flag is enabled. Use the migration action in the KB detail view.
- If `--profile kg-rag` is omitted, Neo4j will not start.
- If `KG_RAG_NEO4J_URI=bolt://localhost:7687` is used inside Docker, the KB container will point at itself instead of Neo4j. Use `bolt://neo4j:7687` in Docker.
- If the KB `/health` endpoint gives an empty reply right after recreation, wait for dependency installation to finish and check `docker logs lamb-kb-1`.
- If graph ingestion succeeds partially or fails, vector ingestion can still succeed; check the returned `kg_rag` status and KB logs.

## Feature Test Flow

1. Open `http://localhost:5173/` and log in.
2. Create a knowledge base and enable the Graph RAG checkbox, or open an existing KB and use **Migrate to Graph RAG**.
3. Ingest documents while `KG_RAG_ENABLED=true`, `KG_RAG_INDEX_ON_INGEST=true`, and the KB has `graph_enabled=true`.
4. Open the collection detail view and use the Graph tab to inspect concepts, documents, chunks, and changes. The document filter searches by filename.
5. Use the Benchmarks tab to compare `simple_query` against `kg_rag_query`.
6. For API testing, query the KB with `plugin_name=kg_rag_query` and enable trace metadata in `plugin_params`.

The migration endpoint used by the UI is:

```bash
curl -X POST -H "Authorization: Bearer <creator-token>" \
  http://localhost:9099/creator/graph/collections/<kb-id>/migrate
```

This sets `graph_enabled=true` for the collection and indexes the existing Chroma chunks into Neo4j.

More technical details are in `lamb-kb-server-stable/Docs/kg-rag-pipeline.md`.
