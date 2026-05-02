"""Focused KG-RAG tests that do not require OpenAI or Neo4j."""

from services.concept_extraction import (
    ConceptExtractor,
    TextChunk,
    normalize_concept,
    normalize_relation,
)
from plugins.kg_rag_query import KGRAGQueryPlugin
from services.graph_store import GraphStore


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
