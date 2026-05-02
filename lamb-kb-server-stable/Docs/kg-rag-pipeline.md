# KG-RAG Pipeline

LAMB includes an optional KG-RAG pipeline that augments the existing ChromaDB vector retrieval with LLM-extracted entities, typed relationships, and Neo4j graph expansion. The feature is disabled by default, so existing deployments continue to ingest and query with the normal vector path unless explicitly enabled.

## Configuration

Set these environment variables in the KB server environment:

```env
KG_RAG_ENABLED=true
KG_RAG_INDEX_ON_INGEST=true
KG_RAG_OPENAI_API_KEY=your-openai-key
KG_RAG_CHAT_MODEL=gpt-4o-mini
KG_RAG_EXTRACTION_MODEL=
KG_RAG_NEO4J_URI=bolt://localhost:7687
KG_RAG_NEO4J_USER=neo4j
KG_RAG_NEO4J_PASSWORD=your-password
KG_RAG_GRAPH_DEPTH=2
KG_RAG_LIMIT_FACTOR=4
PLUGIN_KG_RAG_QUERY=ADVANCED
```

`KG_RAG_OPENAI_API_KEY` falls back to `OPENAI_API_KEY` and then `EMBEDDINGS_APIKEY`. `KG_RAG_EXTRACTION_MODEL` falls back to `OPENAI_EXTRACTION_MODEL`, then `KG_RAG_CHAT_MODEL`, then `OPENAI_CHAT_MODEL`.

## Docker Compose

The root Docker Compose files include a Neo4j service behind the `kg-rag` profile. Start the packaged stack with graph support like this:

```bash
KG_RAG_ENABLED=true \
KG_RAG_OPENAI_API_KEY=your-openai-key \
KG_RAG_NEO4J_PASSWORD=change-this-password \
docker compose -f docker-compose.next.yaml --profile kg-rag up -d kb neo4j
```

For the local development compose file, use the same profile:

```bash
KG_RAG_ENABLED=true \
KG_RAG_OPENAI_API_KEY=your-openai-key \
KG_RAG_NEO4J_PASSWORD=change-this-password \
docker compose -f docker-compose-example.yaml --profile kg-rag up kb neo4j
```

Inside Docker, the KB server connects to `bolt://neo4j:7687`. Neo4j Browser is exposed on `http://localhost:7474` and Bolt on `localhost:7687`.

## Ingestion

After documents are successfully written to ChromaDB, LAMB builds graph chunks using the generated Chroma `document_id` metadata as the Neo4j `Chunk.chunk_id`. If KG-RAG is enabled and OpenAI/Neo4j are configured, the server extracts entities and relationships from parent text units and writes:

- `Organization`, `Collection`, `Document`, `Chunk`, `Concept`, and `ChangeEvent` nodes.
- `OWNS`, `CONTAINS`, `MENTIONS`, `RELATES_TO`, `CO_OCCURS_WITH`, and `RECORDED_CHANGE` relationships.

Graph indexing is best-effort. If extraction or Neo4j fails, vector ingestion still succeeds and the ingestion response includes a `kg_rag` status object.

## Querying

Use the existing collection query endpoint with the new query plugin:

```bash
curl -X POST 'http://localhost:9090/collections/1/query?plugin_name=kg_rag_query' \
  -H 'Authorization: Bearer 0p3n-w3bu!' \
  -H 'Content-Type: application/json' \
  -d '{
    "query_text": "How does traceability improve KG-RAG?",
    "top_k": 5,
    "threshold": 0.0,
    "plugin_params": {
      "graph_depth": 2,
      "return_parent_context": true,
      "include_trace": true
    }
  }'
```

The plugin first runs the same ChromaDB vector search semantics as `simple_query`, then uses seed chunk IDs to find mentioned concepts in Neo4j. It traverses typed `RELATES_TO` paths up to `graph_depth` and uses one-hop `CO_OCCURS_WITH` as a lower-weight fallback. Expanded chunk IDs are fetched back from ChromaDB, merged with baseline results, and returned in the existing `{similarity, data, metadata}` shape.

When `include_trace` is true, each result includes `metadata.kg_rag` with seed IDs, entry concepts, traversed edges, expanded IDs, recent graph changes, latency, and fallback warnings.

## Fallback Behavior

If KG-RAG is disabled, Neo4j is not configured, no seed chunks are found, or graph expansion returns no chunks, `kg_rag_query` returns the vector baseline results. The fallback reason is included in `metadata.kg_rag.warnings` when trace metadata is enabled.

## Traceability Endpoints

The graph traceability API is exposed under `/graph` and uses the same bearer-token authentication as the rest of the KB server.

- `GET /graph/collections/{collection_id}/changes`: list graph change history for a collection. Optional filters: `concept`, `document_id`, `filename`, `operation`, and `limit`.
- `GET /graph/collections/{collection_id}/changes/{event_id}`: inspect a single `ChangeEvent`, including related graph chunks when available.
- `GET /graph/collections/{collection_id}/concepts/{concept}/changes`: inspect changes touching a concept.
- `GET /graph/collections/{collection_id}/documents/{document_id}/changes`: inspect changes linked to a graph document.
- `POST /graph/collections/{collection_id}/audit-trace`: replay graph expansion from seed chunk IDs and return entry concepts, traversed edges, expanded chunk IDs, recent changes, and graph latency.
- `POST /graph/collections/{collection_id}/changes/{event_id}/revert`: revert supported graph changes. The current implementation supports automatic ingestion events by removing the graph document/chunks, reversing stored relationship/co-occurrence weights when detailed payloads are available, and recording a new `revert_change` event.

## Manual Curation Endpoints

Manual curation operations are collection-scoped and every successful operation records a `ChangeEvent` with a `manual_*` operation name.

- `PATCH /graph/collections/{collection_id}/concepts/{concept}/rename`: rename a concept within the selected collection by rewriting its mentions and collection-scoped relationships.
- `POST /graph/collections/{collection_id}/concepts/merge`: merge one or more source concepts into a target concept.
- `PATCH /graph/collections/{collection_id}/relationships`: edit a `RELATES_TO` relationship relation value, weight, description, evidence, notes, tags, or verification state.
- `PATCH /graph/collections/{collection_id}/concepts/{concept}/curation`: add or update concept notes, tags, and verification state.
- `PATCH /graph/collections/{collection_id}/relationships/curation`: add or update relationship notes, tags, and verification state without changing relation type or weight.
