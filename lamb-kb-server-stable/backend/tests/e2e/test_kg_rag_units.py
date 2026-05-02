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
