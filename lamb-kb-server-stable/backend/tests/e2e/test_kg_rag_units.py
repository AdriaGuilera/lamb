"""Focused KG-RAG tests that do not require OpenAI or Neo4j."""

import importlib
import os
import sys
import types

import pytest

from services.concept_extraction import (
    ConceptExtractor,
    ExtractedEntity,
    ExtractedRelationship,
    GraphExtraction,
    TextChunk,
    normalize_concept,
    normalize_relation,
)
from plugins.kg_rag_query import KGRAGQueryPlugin
from services.graph_store import GraphStore
from services.benchmark import BenchmarkService
from schemas.benchmark import BenchmarkQuestion


def _install_sqlalchemy_stub(monkeypatch):
    sqlalchemy_module = types.ModuleType("sqlalchemy")
    orm_module = types.ModuleType("sqlalchemy.orm")
    orm_module.Session = object
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy_module)
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm", orm_module)


def _install_database_stubs(monkeypatch, chroma_client=None, collection=None):
    _install_sqlalchemy_stub(monkeypatch)

    connection_module = types.ModuleType("database.connection")
    connection_module.get_chroma_client = lambda: chroma_client
    connection_module.get_embedding_function = lambda db_collection: (
        lambda texts: [[0.1, 0.2, 0.3] for _text in texts]
    )
    connection_module.get_embedding_function_by_params = lambda **kwargs: (
        lambda texts: [[0.1, 0.2, 0.3] for _text in texts]
    )
    monkeypatch.setitem(sys.modules, "database.connection", connection_module)

    models_module = types.ModuleType("database.models")
    models_module.Collection = object
    models_module.FileRegistry = object
    models_module.FileStatus = types.SimpleNamespace(COMPLETED="completed")
    monkeypatch.setitem(sys.modules, "database.models", models_module)

    service_module = types.ModuleType("database.service")

    class FakeCollectionService:
        @staticmethod
        def get_collection(db, collection_id):
            return collection

    service_module.CollectionService = FakeCollectionService
    monkeypatch.setitem(sys.modules, "database.service", service_module)
    return service_module


def _import_ingestion_service_with_stubs(
    monkeypatch, chroma_client=None, collection=None
):
    _install_database_stubs(
        monkeypatch,
        chroma_client=chroma_client,
        collection=collection,
    )
    sys.modules.pop("services.ingestion", None)
    module = importlib.import_module("services.ingestion")
    monkeypatch.delitem(sys.modules, "services.ingestion", raising=False)
    return module


def _import_simple_query_with_stubs(monkeypatch):
    _install_database_stubs(monkeypatch)
    sys.modules.pop("plugins.simple_query", None)
    module = importlib.import_module("plugins.simple_query")
    monkeypatch.delitem(sys.modules, "plugins.simple_query", raising=False)
    return module


class FakeResult:
    def __init__(self, rows=None, single_row=None):
        self.rows = rows or []
        self.single_row = single_row

    def data(self):
        return self.rows

    def single(self):
        return self.single_row


class GraphStoreSuccessTx:
    def __init__(self):
        self.queries = []

    def run(self, query, **params):
        self.queries.append({"query": query, "params": params})
        compact_query = " ".join(query.split())
        if "RETURN event.event_id AS revert_event_id" in compact_query:
            return FakeResult(single_row={"revert_event_id": "revert-1"})
        if "RETURN event.event_id AS event_id" in compact_query:
            return FakeResult(single_row={"event_id": "event-1"})
        if "RETURN collect(chunk.chunk_id) AS chunk_ids" in compact_query:
            return FakeResult(single_row={"chunk_ids": ["chunk-1", "chunk-2"]})
        if "RETURN event.operation AS operation" in compact_query:
            return FakeResult(
                single_row={
                    "operation": "automatic_ingestion",
                    "filename": "kg.md",
                    "concepts": ["knowledge graph"],
                    "payload_json": '{"relationship_details":[{"source":"knowledge graph","target":"neo4j","relation":"stored_in","confidence":0.5}],"cooccurrence_details":[{"source":"knowledge graph","target":"neo4j"}]}',
                    "document_id": "doc-1",
                }
            )
        if "RETURN rel.weight AS old_weight" in compact_query:
            return FakeResult(
                single_row={
                    "old_weight": 1.0,
                    "old_description": "old",
                    "old_evidence": "old evidence",
                    "old_notes": "old notes",
                    "old_tags": ["old"],
                    "old_verification_state": "unverified",
                }
            )
        if "RETURN concept.name AS name" in compact_query:
            return FakeResult(
                single_row={
                    "name": params.get("concept", "knowledge graph"),
                    "old_notes": "old notes",
                    "old_tags": ["old"],
                    "old_verification_state": "unverified",
                }
            )
        if "RETURN count(" in compact_query or "RETURN count(rel) AS count" in compact_query:
            return FakeResult(single_row={"count": 1})
        return FakeResult()


class GraphStoreCoverageSession:
    def __init__(self):
        self.queries = []
        self.tx = GraphStoreSuccessTx()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute_write(self, callback, *args):
        return callback(self.tx, *args)

    def run(self, query, **params):
        self.queries.append({"query": query, "params": params})
        compact_query = " ".join(query.split())
        if "CREATE CONSTRAINT" in compact_query or "CREATE INDEX" in compact_query:
            return FakeResult()
        if "RETURN event.event_id AS event_id" in compact_query and "chunk_ids" not in compact_query:
            return FakeResult(
                rows=[
                    {
                        "event_id": "event-1",
                        "collection_id": params.get("collection_id", 1),
                        "org_id": params.get("org_id", "org-1"),
                        "operation": "automatic_ingestion",
                        "actor": "pytest",
                        "timestamp": "2026-05-02T00:00:00Z",
                        "filename": "kg.md",
                        "concepts": ["knowledge graph"],
                        "payload_json": "{}",
                        "document_id": "doc-1",
                        "file_id": 2,
                    }
                ]
            )
        if "RETURN event.event_id AS event_id" in compact_query and "chunk_ids" in compact_query:
            return FakeResult(
                single_row={
                    "event_id": "event-1",
                    "collection_id": params.get("collection_id", 1),
                    "org_id": params.get("org_id", "org-1"),
                    "operation": "automatic_ingestion",
                    "actor": "pytest",
                    "timestamp": "2026-05-02T00:00:00Z",
                    "filename": "kg.md",
                    "concepts": ["knowledge graph"],
                    "payload_json": "{}",
                    "document_id": "doc-1",
                    "file_id": 2,
                    "chunk_ids": ["chunk-1"],
                }
            )
        if "RETURN concept.name AS name," in compact_query and "chunk_count" in compact_query:
            return FakeResult(
                rows=[
                    {
                        "name": "knowledge graph",
                        "display_name": "Knowledge Graph",
                        "entity_type": "concept",
                        "description": "Graph concepts",
                        "notes": "curated",
                        "tags": ["kg"],
                        "verification_state": "verified",
                        "chunk_count": 2,
                    },
                    {
                        "name": "neo4j",
                        "display_name": "Neo4j",
                        "entity_type": "technology",
                        "description": "Graph database",
                        "notes": "",
                        "tags": [],
                        "verification_state": None,
                        "chunk_count": 1,
                    },
                ]
            )
        if "RETURN source.name AS source" in compact_query:
            return FakeResult(
                rows=[
                    {
                        "source": "knowledge graph",
                        "target": "neo4j",
                        "type": "RELATES_TO",
                        "relation": "stored_in",
                        "weight": 2.0,
                        "description": "stored in",
                        "evidence": "evidence",
                        "notes": "",
                        "tags": ["manual"],
                        "verification_state": "verified",
                    }
                ]
            )
        if "RETURN chunk.chunk_id AS chunk_id" in compact_query and "text_preview" in compact_query:
            return FakeResult(
                rows=[
                    {
                        "chunk_id": "chunk-1",
                        "source_label": "Section 1",
                        "filename": "kg.md",
                        "document_id": "doc-1",
                        "text_preview": "Knowledge graph text",
                        "concepts": ["knowledge graph", "neo4j"],
                    }
                ]
            )
        if "RETURN concept.name AS name, count(*) AS mentions" in compact_query:
            return FakeResult(rows=[{"name": "knowledge graph", "mentions": 1}])
        if "RETURN entry.name AS entry" in compact_query:
            return FakeResult(
                rows=[
                    {
                        "entry": "knowledge graph",
                        "related": "neo4j",
                        "edges": [
                            {
                                "source": "knowledge graph",
                                "target": "neo4j",
                                "type": "stored_in",
                                "weight": 1,
                            }
                        ],
                        "chunk_ids": ["chunk-2"],
                        "hops": 1,
                        "score": 2.0,
                    }
                ]
            )
        if "RETURN chunk.chunk_id AS chunk_id" in compact_query and "mentions" in compact_query:
            return FakeResult(rows=[{"chunk_id": "chunk-1", "mentions": 1, "source_label": "Section"}])
        if "RETURN event.operation AS operation" in compact_query:
            return FakeResult(
                rows=[
                    {
                        "operation": "automatic_ingestion",
                        "actor": "pytest",
                        "timestamp": "2026-05-02T00:00:00Z",
                        "filename": "kg.md",
                        "concepts": ["knowledge graph"],
                        "payload_json": "{}",
                    }
                ]
            )
        return FakeResult()


class GraphStoreCoverageDriver:
    def __init__(self):
        self.session_instance = GraphStoreCoverageSession()
        self.closed = False
        self.verify_calls = 0

    def session(self):
        return self.session_instance

    def verify_connectivity(self):
        self.verify_calls += 1

    def close(self):
        self.closed = True


def _configured_graph_store(monkeypatch):
    graph_store = GraphStore(kg_config={"enabled": False})
    graph_store.driver = GraphStoreCoverageDriver()
    graph_store.enabled = True
    graph_store.uri = "bolt://example"
    graph_store.password = "secret"
    monkeypatch.setattr("services.graph_store.GraphDatabase", object())
    return graph_store


def test_normalize_concept_folds_accents_and_spacing():
    assert normalize_concept("  Traçabilitat   del Graf! ") == "tracabilitat del graf"


def test_normalize_relation_uses_safe_property_value():
    assert normalize_relation("depends on / improves") == "depends_on_improves"


def test_extractor_without_openai_returns_empty_graph_data():
    extractor = ConceptExtractor(
        kg_config={
            "openai_api_key": "",
            "chat_model": "gpt-test",
            "extraction_model": "gpt-test",
        }
    )
    chunk = TextChunk(
        chunk_id="chunk-1",
        text="Neo4j stores a knowledge graph.",
        parent_text="Neo4j stores a knowledge graph.",
        metadata={"section_title": "Document"},
    )

    extraction = extractor.extract_for_chunks([chunk])

    assert extraction.concepts_by_chunk == {"chunk-1": []}
    assert extraction.entities == {}
    assert extraction.relationships == []


def test_parse_payload_extracts_entities_and_relationships():
    extractor = ConceptExtractor(kg_config={"openai_api_key": ""})

    extraction = extractor._parse_payload(
        {
            "entities": [
                {
                    "name": "Knowledge Graph",
                    "type": "Concept",
                    "description": "A graph of entities and relationships.",
                    "confidence": 0.95,
                },
                {
                    "name": "Neo4j",
                    "type": "Technology",
                    "description": "A graph database.",
                    "confidence": 0.9,
                },
            ],
            "relationships": [
                {
                    "source": "Knowledge Graph",
                    "target": "Neo4j",
                    "relation": "stored in",
                    "description": "Neo4j stores graph data.",
                    "evidence": "Neo4j stores a knowledge graph.",
                    "confidence": 0.8,
                }
            ],
        },
        "chunk-1",
    )

    assert sorted(extraction.entities) == ["knowledge graph", "neo4j"]
    assert extraction.entities["neo4j"].entity_type == "technology"
    assert extraction.relationships[0].relation == "stored_in"


def test_kg_rag_query_disabled_returns_baseline_with_trace(monkeypatch):
    class FakeChromaCollection:
        def query(self, query_texts, n_results):
            return {
                "documents": [["Vector result text"]],
                "metadatas": [[{"document_id": "chunk-1"}]],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": False, "graph_depth": 2, "limit_factor": 4},
    )

    results = KGRAGQueryPlugin().query(
        collection_id=1,
        query_text="test query",
        top_k=5,
        threshold=0.0,
        db=object(),
        chroma_collection=FakeChromaCollection(),
    )

    assert len(results) == 1
    assert results[0]["similarity"] == 0.9
    assert results[0]["metadata"]["kg_rag"]["enabled"] is False
    assert "KG-RAG is disabled" in results[0]["metadata"]["kg_rag"]["warnings"][0]


def test_kg_rag_query_plugin_parameters_are_exposed():
    params = KGRAGQueryPlugin().get_parameters()

    assert params["top_k"]["default"] == 5
    assert params["graph_depth"]["default"] == 2
    assert params["include_trace"]["type"] == "boolean"


def test_kg_rag_query_requires_db_and_chroma_collection():
    plugin = KGRAGQueryPlugin()

    with pytest.raises(ValueError, match="Database session is required"):
        plugin.query(collection_id=1, query_text="x", chroma_collection=object())

    with pytest.raises(ValueError, match="ChromaDB collection is required"):
        plugin.query(collection_id=1, query_text="x", db=object())


def test_kg_rag_query_enabled_without_seed_chunks_returns_traced_empty_baseline(monkeypatch):
    class FakeChromaCollection:
        def query(self, query_texts, n_results):
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": True, "graph_depth": 2, "limit_factor": 4},
    )

    results = KGRAGQueryPlugin().query(
        collection_id=1,
        query_text="no seeds",
        db=object(),
        chroma_collection=FakeChromaCollection(),
    )

    assert results == []


def test_kg_rag_query_missing_collection_raises(monkeypatch):
    class FakeChromaCollection:
        def query(self, query_texts, n_results):
            return {
                "documents": [["Seed"]],
                "metadatas": [[{"document_id": "seed-1"}]],
                "distances": [[0.1]],
            }

    service_module = types.ModuleType("database.service")

    class FakeCollectionService:
        @staticmethod
        def get_collection(db, collection_id):
            return None

    service_module.CollectionService = FakeCollectionService
    monkeypatch.setitem(sys.modules, "database.service", service_module)
    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": True, "graph_depth": 2, "limit_factor": 4},
    )

    with pytest.raises(ValueError, match="Collection with ID 404 not found"):
        KGRAGQueryPlugin().query(
            collection_id=404,
            query_text="seed",
            db=object(),
            chroma_collection=FakeChromaCollection(),
        )


def test_kg_rag_query_unconfigured_graph_returns_baseline_without_trace(monkeypatch):
    class FakeChromaCollection:
        def query(self, query_texts, n_results):
            return {
                "documents": [["Seed"]],
                "metadatas": [[{"document_id": "seed-1"}]],
                "distances": [[0.1]],
            }

    class FakeGraphStore:
        def is_configured(self):
            return False

    service_module = types.ModuleType("database.service")

    class FakeCollection:
        owner = "org-attr"

    class FakeCollectionService:
        @staticmethod
        def get_collection(db, collection_id):
            return FakeCollection()

    service_module.CollectionService = FakeCollectionService
    monkeypatch.setitem(sys.modules, "database.service", service_module)
    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": True, "graph_depth": 2, "limit_factor": 4},
    )
    monkeypatch.setattr("plugins.kg_rag_query.get_graph_store", lambda: FakeGraphStore())

    results = KGRAGQueryPlugin().query(
        collection_id=1,
        query_text="seed",
        db=object(),
        chroma_collection=FakeChromaCollection(),
        include_trace=False,
    )

    assert results == [
        {"similarity": pytest.approx(0.9), "data": "Seed", "metadata": {"document_id": "seed-1"}}
    ]


def test_kg_rag_query_graph_expands_no_additional_chunks_warns(monkeypatch):
    class FakeChromaCollection:
        def query(self, query_texts, n_results):
            return {
                "documents": [["Seed"]],
                "metadatas": [[{"document_id": "seed-1"}]],
                "distances": [[0.1]],
            }

    class FakeGraphStore:
        def is_configured(self):
            return True

        def expand_from_chunks(self, **kwargs):
            return {
                "entry_concepts": ["seed"],
                "traversed_edges": [],
                "expanded_chunk_ids": [],
                "latest_changes": [],
                "graph_latency_ms": 1.0,
            }

    service_module = types.ModuleType("database.service")

    class FakeCollectionService:
        @staticmethod
        def get_collection(db, collection_id):
            return {"id": collection_id, "owner": "org-1"}

    service_module.CollectionService = FakeCollectionService
    monkeypatch.setitem(sys.modules, "database.service", service_module)
    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": True, "graph_depth": 2, "limit_factor": 4},
    )
    monkeypatch.setattr("plugins.kg_rag_query.get_graph_store", lambda: FakeGraphStore())

    results = KGRAGQueryPlugin().query(
        collection_id=1,
        query_text="seed",
        db=object(),
        chroma_collection=FakeChromaCollection(),
    )

    assert results[0]["metadata"]["kg_rag"]["warnings"] == [
        "Graph returned no additional chunks"
    ]


def test_kg_rag_query_private_helpers_cover_edge_cases():
    plugin = KGRAGQueryPlugin()

    assert plugin._as_bool(True) is True
    assert plugin._as_bool(False) is False
    assert plugin._as_bool(None) is False
    assert plugin._as_bool("enabled") is True
    assert plugin._as_bool("off") is False
    assert plugin._result_chunk_id({"metadata": {"child_chunk_id": "child-1"}}) == "child-1"
    assert plugin._result_chunk_id({"metadata": {"chunk_id": "chunk-1"}}) == "chunk-1"

    class MismatchedChromaCollection:
        def query(self, query_texts, n_results):
            return {
                "documents": [["doc-1", "doc-2"]],
                "metadatas": [[{"document_id": "doc-1"}]],
                "distances": [[0.1]],
            }

    assert plugin._query_vector_baseline(
        MismatchedChromaCollection(), "query", top_k=2, threshold=0.0
    ) == [
        {"similarity": pytest.approx(0.9), "data": "doc-1", "metadata": {"document_id": "doc-1"}}
    ]

    class RaisingChromaCollection:
        def get(self, ids, include):
            raise RuntimeError("boom")

    assert plugin._fetch_expanded_results(RaisingChromaCollection(), ["x"], True) == []

    class SparseChromaCollection:
        def get(self, ids, include):
            return {"ids": [], "documents": [], "metadatas": []}

    sparse_results = plugin._fetch_expanded_results(
        SparseChromaCollection(), ["expanded-1"], return_parent_context=False
    )
    assert sparse_results == [
        {
            "similarity": 0.72,
            "data": "",
            "metadata": {"document_id": "expanded-1", "kg_rag_origin": "graph_expansion"},
        }
    ]

    merged = plugin._merge_results(
        [{"similarity": 0.1, "data": "fallback key", "metadata": {}}], top_k=0
    )
    assert merged[0]["data"] == "fallback key"
    assert plugin._attach_trace([{"metadata": {}}], {"mode": "kg_rag"}, False) == [
        {"metadata": {}}
    ]
    assert KGRAGQueryPlugin().name == "kg_rag_query"


def test_kg_rag_merge_prefers_highest_similarity():
    plugin = KGRAGQueryPlugin()

    merged = plugin._merge_results(
        [
            {"similarity": 0.7, "data": "older", "metadata": {"document_id": "c1"}},
            {"similarity": 0.8, "data": "newer", "metadata": {"document_id": "c1"}},
            {"similarity": 0.72, "data": "graph", "metadata": {"document_id": "c2"}},
        ],
        top_k=5,
    )

    assert [item["metadata"]["document_id"] for item in merged] == ["c1", "c2"]
    assert merged[0]["data"] == "newer"


def test_kg_rag_query_expands_graph_chunks_and_attaches_trace(monkeypatch):
    class FakeChromaCollection:
        def __init__(self):
            self.get_calls = []

        def query(self, query_texts, n_results):
            return {
                "documents": [["Seed chunk text"]],
                "metadatas": [[{"document_id": "seed-1", "filename": "seed.md"}]],
                "distances": [[0.08]],
            }

        def get(self, ids, include):
            self.get_calls.append({"ids": ids, "include": include})
            return {
                "ids": ids,
                "documents": ["Expanded chunk text"],
                "metadatas": [
                    {
                        "document_id": "expanded-1",
                        "filename": "expanded.md",
                        "parent_text": "Expanded parent context",
                    }
                ],
            }

    class FakeGraphStore:
        def is_configured(self):
            return True

        def expand_from_chunks(self, **kwargs):
            assert kwargs["collection_id"] == 7
            assert kwargs["org_id"] == "org-1"
            assert kwargs["seed_chunk_ids"] == ["seed-1"]
            assert kwargs["depth"] == 3
            return {
                "entry_concepts": ["knowledge graph"],
                "traversed_edges": [
                    {
                        "source": "knowledge graph",
                        "target": "neo4j",
                        "type": "stored_in",
                    }
                ],
                "expanded_chunk_ids": ["seed-1", "expanded-1"],
                "latest_changes": [{"operation": "automatic_ingestion"}],
                "graph_latency_ms": 6.5,
            }

    service_module = types.ModuleType("database.service")

    class FakeCollectionService:
        @staticmethod
        def get_collection(db, collection_id):
            return {"id": collection_id, "owner": "org-1", "name": "kb"}

    service_module.CollectionService = FakeCollectionService
    monkeypatch.setitem(sys.modules, "database.service", service_module)
    monkeypatch.setattr(
        "plugins.kg_rag_query.config_module.get_kg_rag_config",
        lambda: {"enabled": True, "graph_depth": 2, "limit_factor": 4},
    )
    monkeypatch.setattr(
        "plugins.kg_rag_query.get_graph_store",
        lambda: FakeGraphStore(),
    )

    chroma_collection = FakeChromaCollection()
    results = KGRAGQueryPlugin().query(
        collection_id=7,
        query_text="how does kg-rag expand?",
        top_k=2,
        graph_depth=3,
        db=object(),
        chroma_collection=chroma_collection,
    )

    assert chroma_collection.get_calls == [
        {"ids": ["expanded-1"], "include": ["documents", "metadatas"]}
    ]
    assert [result["metadata"]["document_id"] for result in results] == [
        "seed-1",
        "expanded-1",
    ]
    assert results[1]["data"] == "Expanded parent context"
    trace = results[0]["metadata"]["kg_rag"]
    assert trace["graph_expanded"] is True
    assert trace["entry_concepts"] == ["knowledge graph"]
    assert trace["expanded_chunk_ids"] == ["seed-1", "expanded-1"]
    assert trace["graph_latency_ms"] == 6.5


def test_simple_query_with_injected_chroma_keeps_baseline_shape(monkeypatch):
    simple_query_module = _import_simple_query_with_stubs(monkeypatch)

    class FakeChromaCollection:
        def __init__(self):
            self.query_calls = []

        def query(self, query_texts, n_results):
            self.query_calls.append(
                {"query_texts": query_texts, "n_results": n_results}
            )
            return {
                "documents": [["High confidence", "Filtered out"]],
                "metadatas": [[{"document_id": "doc-1"}, {"document_id": "doc-2"}]],
                "distances": [[0.1, 0.8]],
            }

    chroma_collection = FakeChromaCollection()
    results = simple_query_module.SimpleQueryPlugin().query(
        collection_id=1,
        query_text="baseline query",
        top_k=2,
        threshold=0.5,
        db=object(),
        chroma_collection=chroma_collection,
    )

    assert chroma_collection.query_calls == [
        {"query_texts": ["baseline query"], "n_results": 2}
    ]
    assert results == [
        {
            "similarity": pytest.approx(0.9),
            "data": "High confidence",
            "metadata": {"document_id": "doc-1"},
        }
    ]
    assert "kg_rag" not in results[0]["metadata"]


def test_graph_store_ingest_chunks_uses_mocked_driver_session(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.write_calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute_write(self, callback, *args):
            self.write_calls.append({"callback": callback, "args": args})

    class FakeDriver:
        def __init__(self):
            self.session_instance = FakeSession()

        def session(self):
            return self.session_instance

    driver = FakeDriver()
    graph_store = GraphStore(kg_config={"enabled": False})
    graph_store.driver = driver
    monkeypatch.setattr(graph_store, "ensure_schema", lambda: True)

    chunks = [
        TextChunk(
            chunk_id="chunk-1",
            text="Knowledge graphs can use Neo4j.",
            parent_text="Knowledge graphs can use Neo4j.",
            metadata={"filename": "kg.md", "parent_chunk_id": "parent-1"},
        ),
        TextChunk(
            chunk_id="chunk-2",
            text="Neo4j supports graph traversal.",
            parent_text="Neo4j supports graph traversal.",
            metadata={"filename": "kg.md", "parent_chunk_id": "parent-2"},
        ),
    ]

    writes = graph_store.ingest_chunks(
        collection={"id": 5, "owner": "org-1", "name": "KG"},
        file_id=11,
        filename="kg.md",
        chunks=chunks,
        concepts_by_chunk={
            "chunk-1": ["knowledge graph", "neo4j"],
            "chunk-2": ["neo4j"],
        },
        entities={
            "knowledge graph": ExtractedEntity(
                name="knowledge graph",
                display_name="Knowledge Graph",
            ),
            "neo4j": ExtractedEntity(name="neo4j", display_name="Neo4j"),
        },
        relationships=[
            ExtractedRelationship(
                source="knowledge graph",
                target="neo4j",
                relation="stored_in",
                chunk_id="chunk-1",
            )
        ],
    )

    assert writes == 7
    assert len(driver.session_instance.write_calls) == 1
    call = driver.session_instance.write_calls[0]
    assert call["callback"] == GraphStore._ingest_tx
    assert call["args"][0] == {"id": 5, "owner": "org-1", "name": "KG"}
    assert call["args"][1] == 11
    assert call["args"][2] == "kg.md"
    assert call["args"][3] == chunks


def test_graph_store_lifecycle_schema_delete_and_singleton(monkeypatch):
    import services.graph_store as graph_store_module

    assert "T" in graph_store_module.utc_now()

    class DriverFactory:
        created_driver = GraphStoreCoverageDriver()

        @staticmethod
        def driver(uri, auth):
            assert uri == "bolt://kg"
            assert auth == ("neo4j", "pw")
            return DriverFactory.created_driver

    monkeypatch.setattr(graph_store_module, "GraphDatabase", DriverFactory)
    graph_store = GraphStore(
        kg_config={
            "enabled": True,
            "neo4j_uri": "bolt://kg",
            "neo4j_user": "neo4j",
            "neo4j_password": "pw",
        }
    )

    assert graph_store.is_configured() is True
    assert graph_store.is_available() is True
    assert graph_store.ensure_schema() is True
    assert graph_store.ensure_schema() is True
    graph_store.delete_collection(5)
    graph_store.close()
    assert DriverFactory.created_driver.closed is True

    graph_store_module._GRAPH_STORE = None
    monkeypatch.setattr(
        graph_store_module.config_module,
        "get_kg_rag_config",
        lambda: {"enabled": False},
    )
    assert graph_store_module.get_graph_store() is graph_store_module.get_graph_store()
    graph_store_module._GRAPH_STORE = None


def test_graph_store_lifecycle_failure_paths(monkeypatch):
    import services.graph_store as graph_store_module

    class RaisingGraphDatabase:
        @staticmethod
        def driver(uri, auth):
            raise RuntimeError("cannot connect")

    monkeypatch.setattr(graph_store_module, "GraphDatabase", RaisingGraphDatabase)
    graph_store = GraphStore(
        kg_config={
            "enabled": True,
            "neo4j_uri": "bolt://kg",
            "neo4j_user": "neo4j",
            "neo4j_password": "pw",
        }
    )
    assert graph_store.driver is None
    assert graph_store.is_available() is False
    assert graph_store.ensure_schema() is False

    class UnavailableDriver(GraphStoreCoverageDriver):
        def verify_connectivity(self):
            raise RuntimeError("down")

    graph_store.driver = UnavailableDriver()
    assert graph_store.is_available() is False

    class BadSessionDriver(GraphStoreCoverageDriver):
        def session(self):
            raise RuntimeError("schema failed")

    graph_store.driver = BadSessionDriver()
    assert graph_store.ensure_schema() is False


def test_graph_store_read_methods_and_collection_graph(monkeypatch):
    graph_store = _configured_graph_store(monkeypatch)
    graph_store._schema_ready = True

    changes = graph_store.list_changes(
        5,
        "org-1",
        concept="knowledge graph",
        document_id="doc-1",
        filename="kg.md",
        operation="automatic_ingestion",
        limit=500,
    )
    assert changes[0]["event_id"] == "event-1"

    change = graph_store.get_change(5, "org-1", "event-1")
    assert change["chunk_ids"] == ["chunk-1"]

    graph = graph_store.get_collection_graph(
        5,
        "org-1",
        concept="Knowledge",
        document_id="doc-1",
        include_chunks=True,
        limit=500,
    )
    assert graph["counts"] == {"concepts": 2, "chunks": 1, "edges": 3}
    assert {node["type"] for node in graph["nodes"]} == {"concept", "chunk"}

    graph_without_chunks = graph_store.get_collection_graph(
        5,
        "org-1",
        include_chunks=False,
        limit=0,
    )
    assert graph_without_chunks["counts"]["chunks"] == 0

    monkeypatch.setattr(graph_store, "ensure_schema", lambda: False)
    assert graph_store.list_changes(5, "org-1") == []
    assert graph_store.get_change(5, "org-1", "missing") is None
    assert graph_store.delete_collection(5) is None
    empty_graph = graph_store.get_collection_graph(5, "org-1")
    assert empty_graph["counts"] == {"concepts": 0, "chunks": 0, "edges": 0}


def test_graph_store_collection_graph_no_concepts(monkeypatch):
    class EmptyConceptSession(GraphStoreCoverageSession):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN concept.name AS name," in compact_query and "chunk_count" in compact_query:
                return FakeResult(rows=[])
            return super().run(query, **params)

    graph_store = _configured_graph_store(monkeypatch)
    graph_store._schema_ready = True
    graph_store.driver.session_instance = EmptyConceptSession()

    graph = graph_store.get_collection_graph(5, "org-1", concept="missing")
    assert graph["nodes"] == []
    assert graph["counts"]["concepts"] == 0


def test_graph_store_collection_graph_skips_chunk_rows_without_ids(monkeypatch):
    class MissingChunkIdSession(GraphStoreCoverageSession):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN chunk.chunk_id AS chunk_id" in compact_query and "text_preview" in compact_query:
                return FakeResult(rows=[{"chunk_id": "", "concepts": ["knowledge graph"]}])
            return super().run(query, **params)

    graph_store = _configured_graph_store(monkeypatch)
    graph_store._schema_ready = True
    graph_store.driver.session_instance = MissingChunkIdSession()

    graph = graph_store.get_collection_graph(5, "org-1", include_chunks=True)
    assert graph["counts"]["chunks"] == 1
    assert all(node["type"] == "concept" for node in graph["nodes"])


def test_graph_store_curation_wrappers_and_transactions(monkeypatch):
    graph_store = _configured_graph_store(monkeypatch)
    graph_store._schema_ready = True

    rename = graph_store.rename_concept(5, "org-1", "Knowledge Graph", "Graph RAG")
    assert rename["ok"] is True
    assert rename["operation"] == "manual_rename_concept"

    merge = graph_store.merge_concepts(5, "org-1", ["Graph RAG"], "Knowledge Graph")
    assert merge["ok"] is True
    assert merge["details"]["moved"]

    same_relation_edit = graph_store.edit_relationship(
        5,
        "org-1",
        source_name="Knowledge Graph",
        target_name="Neo4j",
        relation="stored in",
        weight=2.0,
        notes="reviewed",
        tags=["manual"],
        verification_state="verified",
    )
    assert same_relation_edit["ok"] is True

    changed_relation_edit = graph_store.edit_relationship(
        5,
        "org-1",
        source_name="Knowledge Graph",
        target_name="Neo4j",
        relation="stored in",
        new_relation="queries",
        description="new relation",
        evidence="manual evidence",
    )
    assert changed_relation_edit["details"]["new_relation"] == "queries"

    curation = graph_store.update_concept_curation(
        5,
        "org-1",
        "Knowledge Graph",
        notes="verified",
        tags=["kg"],
        verification_state="verified",
    )
    assert curation["ok"] is True

    tx = GraphStoreSuccessTx()
    assert GraphStore._rename_concept_tx(tx, 5, "org-1", "", "x", "actor", "", "now") == {
        "ok": False,
        "reason": "invalid_concept_name",
    }
    assert GraphStore._rename_concept_tx(
        tx, 5, "org-1", "Same", "same", "actor", "", "now"
    ) == {"ok": False, "reason": "concept_names_are_equal"}
    assert GraphStore._merge_concepts_tx(
        tx, 5, "org-1", [], "target", "actor", "", "now"
    ) == {"ok": False, "reason": "invalid_merge_request"}
    assert GraphStore._edit_relationship_tx(
        tx,
        5,
        "org-1",
        "",
        "target",
        "related_to",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "actor",
        "",
        "manual_edit_relationship",
        "now",
    ) == {"ok": False, "reason": "invalid_relationship_identity"}
    assert GraphStore._update_concept_curation_tx(
        tx, 5, "org-1", "", None, None, None, "actor", "", "now"
    ) == {"ok": False, "reason": "invalid_concept_name"}

    monkeypatch.setattr(graph_store, "ensure_schema", lambda: False)
    assert graph_store.revert_change(5, "org-1", "event")["reason"] == "neo4j_not_available"
    assert graph_store.rename_concept(5, "org-1", "a", "b")["reason"] == "neo4j_not_available"
    assert graph_store.merge_concepts(5, "org-1", ["a"], "b")["reason"] == "neo4j_not_available"
    assert graph_store.edit_relationship(
        5, "org-1", source_name="a", target_name="b", relation="r"
    )["reason"] == "neo4j_not_available"
    assert graph_store.update_concept_curation(5, "org-1", "a")["reason"] == "neo4j_not_available"


def test_graph_store_transaction_negative_paths():
    class NoRowsTx(GraphStoreSuccessTx):
        def run(self, query, **params):
            return FakeResult()

    no_rows_tx = NoRowsTx()
    assert GraphStore._revert_change_tx(
        no_rows_tx, 5, "org-1", "missing", "actor", "", "now"
    ) == {"reverted": False, "reason": "change_not_found", "event_id": "missing"}
    assert GraphStore._move_concept_in_collection_tx(
        no_rows_tx, 5, "org-1", "missing", "target", "Target", "now"
    ) == {"moved": False, "reason": "source_concept_not_found"}
    assert GraphStore._merge_concepts_tx(
        no_rows_tx, 5, "org-1", ["source"], "target", "actor", "", "now"
    ) == {"ok": False, "reason": "source_concepts_not_found", "missing": ["source"]}
    assert GraphStore._edit_relationship_tx(
        no_rows_tx,
        5,
        "org-1",
        "source",
        "target",
        "related_to",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "actor",
        "",
        "manual_edit_relationship",
        "now",
    ) == {"ok": False, "reason": "relationship_not_found"}
    assert GraphStore._update_concept_curation_tx(
        no_rows_tx, 5, "org-1", "missing", None, None, None, "actor", "", "now"
    ) == {"ok": False, "reason": "concept_not_found"}

    class UnsupportedEventTx(GraphStoreSuccessTx):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN event.operation AS operation" in compact_query:
                return FakeResult(single_row={"operation": "manual_edit_relationship"})
            return super().run(query, **params)

    assert GraphStore._revert_change_tx(
        UnsupportedEventTx(), 5, "org-1", "event-1", "actor", "", "now"
    ) == {
        "reverted": False,
        "reason": "unsupported_operation",
        "event_id": "event-1",
        "operation": "manual_edit_relationship",
    }

    class NoDocumentEventTx(GraphStoreSuccessTx):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN event.operation AS operation" in compact_query:
                return FakeResult(
                    single_row={
                        "operation": "automatic_ingestion",
                        "payload_json": "{}",
                    }
                )
            return super().run(query, **params)

    assert GraphStore._revert_change_tx(
        NoDocumentEventTx(), 5, "org-1", "event-1", "actor", "", "now"
    ) == {"reverted": False, "reason": "change_has_no_document", "event_id": "event-1"}


def test_graph_store_revert_ingest_expand_and_utilities(monkeypatch):
    graph_store = _configured_graph_store(monkeypatch)
    graph_store._schema_ready = True

    revert = graph_store.revert_change(5, "org-1", "event-1", reason="rollback")
    assert revert["reverted"] is True
    assert revert["chunk_ids"] == ["chunk-1", "chunk-2"]

    class FallbackPayloadTx(GraphStoreSuccessTx):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN event.operation AS operation" in compact_query:
                return FakeResult(
                    single_row={
                        "operation": "automatic_ingestion",
                        "filename": "kg.md",
                        "concepts": ["knowledge graph"],
                        "payload_json": '{"relationships":[null,{"source":"knowledge graph","target":"neo4j","relation":"stored_in"}],"cooccurrences":[null,{"source":"knowledge graph","target":"neo4j"}]}',
                        "document_id": "doc-1",
                    }
                )
            return super().run(query, **params)

    assert GraphStore._revert_change_tx(
        FallbackPayloadTx(), 5, "org-1", "event-1", "actor", "", "now"
    )["reverted"] is True

    class BadPayloadTx(FallbackPayloadTx):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN event.operation AS operation" in compact_query:
                return FakeResult(
                    single_row={
                        "operation": "automatic_ingestion",
                        "payload_json": "not json",
                        "document_id": "doc-1",
                    }
                )
            return super().run(query, **params)

    assert GraphStore._revert_change_tx(
        BadPayloadTx(), 5, "org-1", "event-1", "actor", "", "now"
    )["reverted"] is True

    chunks = [
        TextChunk(
            chunk_id="chunk-1",
            text="Knowledge graph text",
            parent_text="Parent text",
            metadata={"filename": "kg.md", "parent_chunk_id": "parent-1", "section_title": "Intro"},
        )
    ]
    tx = GraphStoreSuccessTx()
    GraphStore._ingest_tx(
        tx,
        {"id": 5, "owner": "org-1", "name": "KG", "description": "desc"},
        10,
        "kg.md",
        chunks,
        {"chunk-1": ["knowledge graph", "neo4j"]},
        [
            {
                "name": "knowledge graph",
                "display_name": "Knowledge Graph",
                "entity_type": "concept",
                "description": "desc",
                "confidence": 0.8,
            }
        ],
        [
            {
                "source": "knowledge graph",
                "target": "neo4j",
                "relation": "stored_in",
                "description": "stored",
                "evidence": "evidence",
                "chunk_id": "chunk-1",
                "confidence": 0.5,
            }
        ],
        [("knowledge graph", "neo4j")],
        "actor",
    )
    assert len(tx.queries) >= 6

    writes = graph_store.ingest_chunks(
        collection={"id": 5, "owner": "org-1", "name": "KG"},
        file_id=None,
        filename="kg.md",
        chunks=chunks,
        concepts_by_chunk={"chunk-1": ["neo4j"]},
        entities={},
        relationships=[
            ExtractedRelationship(
                source="knowledge graph", target="neo4j", relation="related_to"
            )
        ],
    )
    assert writes == 5
    assert graph_store.ingest_chunks(
        collection={"id": 5},
        file_id=1,
        filename="empty.md",
        chunks=[],
        concepts_by_chunk={},
        entities={},
        relationships=[],
    ) == 0

    expansion = graph_store.expand_from_chunks(5, "org-1", ["chunk-1"], depth=99, limit=0)
    assert expansion["entry_concepts"] == ["knowledge graph"]
    assert expansion["expanded_chunk_ids"] == ["chunk-1", "chunk-2"]
    assert expansion["traversed_edges"] == [
        {"source": "knowledge graph", "target": "neo4j", "type": "stored_in", "weight": 1}
    ]
    assert graph_store.expand_from_chunks(5, "org-1", [], depth=2, limit=10)[
        "expanded_chunk_ids"
    ] == []

    class NoEntryConceptSession(GraphStoreCoverageSession):
        def run(self, query, **params):
            compact_query = " ".join(query.split())
            if "RETURN concept.name AS name, count(*) AS mentions" in compact_query:
                return FakeResult(rows=[])
            return super().run(query, **params)

    graph_store.driver.session_instance = NoEntryConceptSession()
    assert graph_store.expand_from_chunks(5, "org-1", ["chunk-1"], depth=2, limit=10)[
        "entry_concepts"
    ] == []

    monkeypatch.setattr(graph_store, "ensure_schema", lambda: False)
    skipped = graph_store.expand_from_chunks(5, "org-1", ["chunk-1"], depth=2, limit=10)
    assert skipped["latest_changes"] == [
        {"warning": "Neo4j is not configured or available; KG expansion skipped"}
    ]
    assert graph_store.ingest_chunks(
        collection={"id": 5},
        file_id=1,
        filename="kg.md",
        chunks=chunks,
        concepts_by_chunk={},
        entities={},
        relationships=[],
    ) == 0

    pair_chunks = [
        TextChunk(
            chunk_id="a",
            text="",
            parent_text="",
            metadata={"source": "s", "parent_chunk_id": "p"},
        ),
        TextChunk(
            chunk_id="b",
            text="",
            parent_text="",
            metadata={"source": "s", "parent_chunk_id": "p"},
        ),
    ]
    assert GraphStore._cooccurrences(pair_chunks, {"a": ["a", "b"], "b": ["b", "c"]}) == {
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
    }
    duplicated_edges = [
        {"source": "a", "target": "b", "type": "r", "i": index} for index in range(82)
    ]
    duplicated_edges.extend({"source": f"a-{index}", "target": "b", "type": "r"} for index in range(90))
    assert len(GraphStore._dedupe_edges(duplicated_edges)) == 80


@pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION_TESTS") != "1",
    reason="set RUN_NEO4J_INTEGRATION_TESTS=1 and KG_RAG_NEO4J_* env vars to run",
)
def test_real_neo4j_graph_store_round_trip_when_enabled():
    import services.graph_store as graph_store_module

    if graph_store_module.GraphDatabase is None:
        pytest.skip("neo4j package is not installed")

    uri = os.getenv("KG_RAG_NEO4J_URI") or os.getenv("NEO4J_URI")
    user = os.getenv("KG_RAG_NEO4J_USER") or os.getenv("NEO4J_USER") or "neo4j"
    password = os.getenv("KG_RAG_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD")
    if not uri or not password:
        pytest.skip("Neo4j URI/password env vars are not configured")

    collection_id = int(os.getenv("KG_RAG_NEO4J_TEST_COLLECTION_ID", "909001"))
    store = GraphStore(
        kg_config={
            "enabled": True,
            "neo4j_uri": uri,
            "neo4j_user": user,
            "neo4j_password": password,
        }
    )
    if not store.is_available():
        pytest.skip("Neo4j is not reachable")

    try:
        store.delete_collection(collection_id)
        writes = store.ingest_chunks(
            collection={
                "id": collection_id,
                "owner": "pytest-org",
                "name": "pytest-kg-rag",
                "description": "Neo4j integration test",
            },
            file_id=1,
            filename="neo4j-integration.md",
            chunks=[
                TextChunk(
                    chunk_id="neo4j-seed",
                    text="Knowledge graph retrieval starts here.",
                    parent_text="Knowledge graph retrieval starts here.",
                    metadata={"filename": "neo4j-integration.md"},
                ),
                TextChunk(
                    chunk_id="neo4j-related",
                    text="Neo4j stores related graph evidence.",
                    parent_text="Neo4j stores related graph evidence.",
                    metadata={"filename": "neo4j-integration.md"},
                ),
            ],
            concepts_by_chunk={
                "neo4j-seed": ["knowledge graph"],
                "neo4j-related": ["neo4j"],
            },
            entities={
                "knowledge graph": ExtractedEntity(
                    name="knowledge graph",
                    display_name="Knowledge Graph",
                ),
                "neo4j": ExtractedEntity(name="neo4j", display_name="Neo4j"),
            },
            relationships=[
                ExtractedRelationship(
                    source="knowledge graph",
                    target="neo4j",
                    relation="stored_in",
                    chunk_id="neo4j-seed",
                )
            ],
            actor="pytest",
        )
        expansion = store.expand_from_chunks(
            collection_id=collection_id,
            org_id="pytest-org",
            seed_chunk_ids=["neo4j-seed"],
            depth=2,
            limit=5,
        )

        assert writes > 0
        assert "knowledge graph" in expansion["entry_concepts"]
        assert "neo4j-related" in expansion["expanded_chunk_ids"]
        assert expansion["traversed_edges"]
    finally:
        store.delete_collection(collection_id)
        store.close()


def test_ingestion_hook_indexes_extracted_concepts_with_mocked_graph(monkeypatch):
    collection = {
        "id": 9,
        "name": "kg-test",
        "owner": "org-1",
        "description": "KG test",
        "embeddings_model": {"vendor": "default", "model": "default"},
    }
    ingestion_module = _import_ingestion_service_with_stubs(
        monkeypatch,
        collection=collection,
    )
    captured = {}

    class FakeExtractor:
        def __init__(self, kg_config):
            captured["kg_config"] = kg_config

        def extract_for_chunks(self, chunks):
            captured["chunks"] = chunks
            return GraphExtraction(
                concepts_by_chunk={chunks[0].chunk_id: ["knowledge graph"]},
                entities={
                    "knowledge graph": ExtractedEntity(
                        name="knowledge graph",
                        display_name="Knowledge Graph",
                    )
                },
                relationships=[],
            )

    class FakeGraphStore:
        def is_configured(self):
            return True

        def ingest_chunks(self, **kwargs):
            captured["graph_kwargs"] = kwargs
            return 4

    monkeypatch.setattr(
        "config.get_kg_rag_config",
        lambda: {"enabled": True, "index_on_ingest": True},
    )
    monkeypatch.setattr("services.concept_extraction.ConceptExtractor", FakeExtractor)
    monkeypatch.setattr(
        "services.graph_store.get_graph_store",
        lambda: FakeGraphStore(),
    )

    result = ingestion_module.IngestionService._index_documents_for_kg_rag(
        db=object(),
        db_collection=collection,
        ids=["chroma-id-1"],
        texts=["Knowledge graphs improve multi-hop retrieval."],
        metadatas=[{"filename": "kg.md", "parent_text": "Parent context"}],
    )

    assert result == {
        "enabled": True,
        "indexed": True,
        "chunks": 1,
        "concepts": 1,
        "relationships": 0,
        "graph_writes": 4,
    }
    assert captured["kg_config"] == {"enabled": True, "index_on_ingest": True}
    assert captured["chunks"][0].chunk_id == "chroma-id-1"
    assert captured["chunks"][0].parent_text == "Parent context"
    assert captured["graph_kwargs"]["filename"] == "kg.md"
    assert captured["graph_kwargs"]["concepts_by_chunk"] == {
        "chroma-id-1": ["knowledge graph"]
    }


def test_normal_ingestion_adds_chroma_documents_when_kg_rag_disabled(monkeypatch):
    class FakeChromaCollection:
        metadata = {}

        def __init__(self):
            self.add_calls = []

        def add(self, ids, documents, metadatas):
            self.add_calls.append(
                {"ids": ids, "documents": documents, "metadatas": metadatas}
            )

    class FakeChromaClient:
        def __init__(self, collection):
            self.collection = collection

        def list_collections(self):
            return ["normal-kb"]

        def get_collection(self, name, embedding_function=None):
            assert name == "normal-kb"
            assert embedding_function is not None
            return self.collection

    collection = {
        "id": 10,
        "name": "normal-kb",
        "owner": "org-1",
        "description": "Normal ingestion",
        "embeddings_model": {"vendor": "default", "model": "default"},
    }
    chroma_collection = FakeChromaCollection()
    ingestion_module = _import_ingestion_service_with_stubs(
        monkeypatch,
        chroma_client=FakeChromaClient(chroma_collection),
        collection=collection,
    )
    monkeypatch.setattr(
        "config.get_kg_rag_config",
        lambda: {"enabled": False, "index_on_ingest": True},
    )

    result = ingestion_module.IngestionService.add_documents_to_collection(
        db=object(),
        collection_id=10,
        documents=[
            {"text": "First normal chunk", "metadata": {"filename": "normal.md"}},
            {"text": "Second normal chunk", "metadata": {"filename": "normal.md"}},
        ],
    )

    assert result["success"] is True
    assert result["documents_added"] == 2
    assert result["kg_rag"] == {
        "enabled": False,
        "indexed": False,
        "reason": "disabled",
    }
    assert len(chroma_collection.add_calls) == 1
    add_call = chroma_collection.add_calls[0]
    assert add_call["documents"] == ["First normal chunk", "Second normal chunk"]
    assert len(add_call["ids"]) == 2
    assert all(
        metadata["filename"] == "normal.md" for metadata in add_call["metadatas"]
    )
    assert all("document_id" in metadata for metadata in add_call["metadatas"])
    assert all("kg_rag" not in metadata for metadata in add_call["metadatas"])


def test_revert_change_reports_missing_event():
    class FakeTx:
        def run(self, query, **params):
            class Result:
                def single(self):
                    return None

            return Result()

    result = GraphStore._revert_change_tx(
        FakeTx(),
        collection_id=1,
        org_id="owner",
        event_id="missing",
        actor="test",
        reason="test",
        timestamp="2026-05-02T00:00:00Z",
    )

    assert result == {
        "reverted": False,
        "reason": "change_not_found",
        "event_id": "missing",
    }


def test_revert_change_rejects_unsupported_operation():
    class FakeRecord(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeTx:
        def run(self, query, **params):
            class Result:
                def single(self):
                    return FakeRecord(operation="manual_edit", document_id="doc-1")

            return Result()

    result = GraphStore._revert_change_tx(
        FakeTx(),
        collection_id=1,
        org_id="owner",
        event_id="event-1",
        actor="test",
        reason="test",
        timestamp="2026-05-02T00:00:00Z",
    )

    assert result["reverted"] is False
    assert result["reason"] == "unsupported_operation"
    assert result["operation"] == "manual_edit"


def test_rename_concept_missing_source_returns_not_found():
    class FakeTx:
        def run(self, query, **params):
            class Result:
                def single(self):
                    return None

            return Result()

    result = GraphStore._rename_concept_tx(
        FakeTx(),
        collection_id=1,
        org_id="owner",
        old_name="Old Concept",
        new_name="New Concept",
        actor="test",
        reason="test",
        timestamp="2026-05-02T00:00:00Z",
    )

    assert result["ok"] is False
    assert result["reason"] == "source_concept_not_found"


def test_merge_concepts_rejects_empty_effective_sources():
    class FakeTx:
        pass

    result = GraphStore._merge_concepts_tx(
        FakeTx(),
        collection_id=1,
        org_id="owner",
        source_names=["Target Concept"],
        target_name="Target Concept",
        actor="test",
        reason="test",
        timestamp="2026-05-02T00:00:00Z",
    )

    assert result == {"ok": False, "reason": "invalid_merge_request"}


def test_edit_relationship_missing_relationship_returns_not_found():
    class FakeTx:
        def run(self, query, **params):
            class Result:
                def single(self):
                    return None

            return Result()

    result = GraphStore._edit_relationship_tx(
        FakeTx(),
        collection_id=1,
        org_id="owner",
        source_name="Knowledge Graph",
        target_name="Neo4j",
        relation="stored in",
        new_relation="implemented by",
        weight=2.0,
        description=None,
        evidence=None,
        notes=None,
        tags=None,
        verification_state=None,
        actor="test",
        reason="test",
        operation="manual_edit_relationship",
        timestamp="2026-05-02T00:00:00Z",
    )

    assert result == {"ok": False, "reason": "relationship_not_found"}


def test_collection_graph_unconfigured_returns_empty_snapshot():
    graph_store = GraphStore(kg_config={"enabled": False})

    snapshot = graph_store.get_collection_graph(
        collection_id=1,
        org_id="owner",
        concept="Knowledge Graph",
        document_id="doc-1",
        include_chunks=True,
        limit=20,
    )

    assert snapshot["collection_id"] == 1
    assert snapshot["nodes"] == []
    assert snapshot["edges"] == []
    assert snapshot["filters"]["concept"] == "Knowledge Graph"
    assert snapshot["counts"] == {"concepts": 0, "chunks": 0, "edges": 0}


def test_benchmark_scores_precision_recall_mrr_by_filename():
    question = BenchmarkQuestion(
        id="q1",
        question="Which files are relevant?",
        relevant_files=["alpha.md", "beta.md"],
    )
    response = {
        "results": [
            {"metadata": {"filename": "noise.md"}},
            {"metadata": {"filename": "alpha.md"}},
            {"metadata": {"source": "/tmp/beta.md"}},
        ]
    }

    score = BenchmarkService._score_response(
        question=question,
        response=response,
        top_k=3,
        vector_ms=12.0,
        graph_ms=4.0,
        total_ms=18.0,
    )

    assert score.precision_at_k == 2 / 3
    assert score.recall_at_k == 1.0
    assert score.mrr == 0.5
    assert score.retrieved_files == ["noise.md", "alpha.md", "beta.md"]
    assert score.vector_ms == 12.0
    assert score.graph_ms == 4.0


def test_benchmark_dataset_aliases_include_control_and_adversarial_sets():
    control = BenchmarkService.get_dataset("no-connections")
    adversarial = BenchmarkService.get_dataset("adversarial")

    assert control.id == "control"
    assert "parity" in control.expected_behavior
    assert adversarial.id == "extreme"
    assert adversarial.recommended_graph_depth == 4
