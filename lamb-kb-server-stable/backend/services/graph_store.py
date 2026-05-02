"""Neo4j storage and traversal service for optional KG-RAG."""

from __future__ import annotations

import itertools
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import config as config_module
from services.concept_extraction import (
    ExtractedEntity,
    ExtractedRelationship,
    TextChunk,
)

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - handled when dependency is not installed yet
    GraphDatabase = None


logger = logging.getLogger("lamb-kb")
_GRAPH_STORE: Optional["GraphStore"] = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_graph_store() -> "GraphStore":
    global _GRAPH_STORE
    if _GRAPH_STORE is None:
        _GRAPH_STORE = GraphStore()
    return _GRAPH_STORE


class GraphStore:
    """Small Neo4j wrapper used by ingestion and KG-RAG query plugins."""

    def __init__(self, kg_config: Optional[Dict[str, Any]] = None):
        self.config = kg_config or config_module.get_kg_rag_config()
        self.enabled = bool(self.config.get("enabled"))
        self.uri = self.config.get("neo4j_uri") or ""
        self.user = self.config.get("neo4j_user") or "neo4j"
        self.password = self.config.get("neo4j_password") or ""
        self.driver = None
        self._schema_ready = False

        if self.is_configured():
            try:
                self.driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password),
                )
            except Exception as exc:
                logger.warning("KG-RAG Neo4j driver could not be created: %s", exc)

    def is_configured(self) -> bool:
        return bool(
            self.enabled and GraphDatabase and self.uri and self.user and self.password
        )

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()

    def is_available(self) -> bool:
        if self.driver is None:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception as exc:
            logger.warning("KG-RAG Neo4j is unavailable: %s", exc)
            return False

    def ensure_schema(self) -> bool:
        if self._schema_ready:
            return True
        if not self.is_available():
            return False

        statements = [
            "CREATE CONSTRAINT org_id IF NOT EXISTS FOR (o:Organization) REQUIRE o.org_id IS UNIQUE",
            "CREATE CONSTRAINT collection_id IF NOT EXISTS FOR (c:Collection) REQUIRE c.collection_id IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.document_id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
            "CREATE CONSTRAINT concept_key IF NOT EXISTS FOR (c:Concept) REQUIRE (c.org_id, c.name) IS UNIQUE",
            "CREATE INDEX concept_collection IF NOT EXISTS FOR (c:Concept) ON (c.org_id)",
            "CREATE INDEX chunk_collection IF NOT EXISTS FOR (c:Chunk) ON (c.collection_id)",
            "CREATE INDEX change_collection IF NOT EXISTS FOR (e:ChangeEvent) ON (e.collection_id)",
        ]
        try:
            with self.driver.session() as session:
                for statement in statements:
                    session.run(statement)
            self._schema_ready = True
            return True
        except Exception as exc:
            logger.warning("KG-RAG Neo4j schema setup failed: %s", exc)
            return False

    def delete_collection(self, collection_id: int) -> None:
        if not self.ensure_schema():
            return
        with self.driver.session() as session:
            session.run(
                """
                MATCH ()-[rel]-()
                WHERE rel.collection_id = $collection_id
                DELETE rel
                """,
                collection_id=collection_id,
            )
            session.run(
                """
                MATCH (node)
                WHERE node.collection_id = $collection_id
                DETACH DELETE node
                """,
                collection_id=collection_id,
            )
            session.run("""
                MATCH (concept:Concept)
                WHERE NOT EXISTS { MATCH (:Chunk)-[:MENTIONS]->(concept) }
                DETACH DELETE concept
                """)

    def list_changes(
        self,
        collection_id: int,
        org_id: str,
        *,
        concept: Optional[str] = None,
        document_id: Optional[str] = None,
        filename: Optional[str] = None,
        operation: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        if not self.ensure_schema():
            return []
        limit = max(1, min(int(limit or 25), 200))
        with self.driver.session() as session:
            return session.run(
                """
                MATCH (event:ChangeEvent {collection_id: $collection_id, org_id: $org_id})
                OPTIONAL MATCH (event)-[:RECORDED_CHANGE]->(doc:Document)
                WHERE ($concept IS NULL OR $concept IN coalesce(event.concepts, []))
                  AND ($document_id IS NULL OR doc.document_id = $document_id)
                  AND ($filename IS NULL OR event.filename = $filename OR doc.filename = $filename)
                  AND ($operation IS NULL OR event.operation = $operation)
                RETURN event.event_id AS event_id,
                       event.collection_id AS collection_id,
                       event.org_id AS org_id,
                       event.operation AS operation,
                       event.actor AS actor,
                       event.timestamp AS timestamp,
                       event.filename AS filename,
                       coalesce(event.concepts, []) AS concepts,
                       event.payload_json AS payload_json,
                       doc.document_id AS document_id,
                       doc.file_id AS file_id
                ORDER BY event.timestamp DESC
                LIMIT $limit
                """,
                collection_id=collection_id,
                org_id=org_id,
                concept=concept,
                document_id=document_id,
                filename=filename,
                operation=operation,
                limit=limit,
            ).data()

    def get_change(
        self, collection_id: int, org_id: str, event_id: str
    ) -> Optional[Dict[str, Any]]:
        if not self.ensure_schema():
            return None
        with self.driver.session() as session:
            row = session.run(
                """
                MATCH (event:ChangeEvent {
                    event_id: $event_id,
                    collection_id: $collection_id,
                    org_id: $org_id
                })
                OPTIONAL MATCH (event)-[:RECORDED_CHANGE]->(doc:Document)
                OPTIONAL MATCH (doc)-[:CONTAINS]->(chunk:Chunk)
                RETURN event.event_id AS event_id,
                       event.collection_id AS collection_id,
                       event.org_id AS org_id,
                       event.operation AS operation,
                       event.actor AS actor,
                       event.timestamp AS timestamp,
                       event.filename AS filename,
                       coalesce(event.concepts, []) AS concepts,
                       event.payload_json AS payload_json,
                       doc.document_id AS document_id,
                       doc.file_id AS file_id,
                       collect(DISTINCT chunk.chunk_id) AS chunk_ids
                """,
                event_id=event_id,
                collection_id=collection_id,
                org_id=org_id,
            ).single()
        return dict(row) if row else None

    def revert_change(
        self,
        collection_id: int,
        org_id: str,
        event_id: str,
        *,
        actor: str = "graph-traceability-api",
        reason: str = "",
    ) -> Dict[str, Any]:
        if not self.ensure_schema():
            return {"reverted": False, "reason": "neo4j_not_available"}
        timestamp = utc_now()
        with self.driver.session() as session:
            return session.execute_write(
                self._revert_change_tx,
                collection_id,
                org_id,
                event_id,
                actor,
                reason,
                timestamp,
            )

    @staticmethod
    def _revert_change_tx(
        tx,
        collection_id: int,
        org_id: str,
        event_id: str,
        actor: str,
        reason: str,
        timestamp: str,
    ) -> Dict[str, Any]:
        event_row = tx.run(
            """
            MATCH (event:ChangeEvent {
                event_id: $event_id,
                collection_id: $collection_id,
                org_id: $org_id
            })
            OPTIONAL MATCH (event)-[:RECORDED_CHANGE]->(doc:Document)
            RETURN event.operation AS operation,
                   event.filename AS filename,
                   coalesce(event.concepts, []) AS concepts,
                   event.payload_json AS payload_json,
                   doc.document_id AS document_id
            """,
            event_id=event_id,
            collection_id=collection_id,
            org_id=org_id,
        ).single()
        if not event_row:
            return {
                "reverted": False,
                "reason": "change_not_found",
                "event_id": event_id,
            }

        operation = event_row.get("operation")
        if operation != "automatic_ingestion":
            return {
                "reverted": False,
                "reason": "unsupported_operation",
                "event_id": event_id,
                "operation": operation,
            }

        document_id = event_row.get("document_id")
        if not document_id:
            return {
                "reverted": False,
                "reason": "change_has_no_document",
                "event_id": event_id,
            }

        try:
            payload = json.loads(event_row.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}

        chunk_row = tx.run(
            """
            MATCH (doc:Document {document_id: $document_id, collection_id: $collection_id})-[:CONTAINS]->(chunk:Chunk)
            RETURN collect(chunk.chunk_id) AS chunk_ids
            """,
            document_id=document_id,
            collection_id=collection_id,
        ).single()
        chunk_ids = list(chunk_row.get("chunk_ids") or []) if chunk_row else []

        relationship_details = payload.get("relationship_details")
        if not isinstance(relationship_details, list):
            relationship_details = payload.get("relationships")
        if not isinstance(relationship_details, list):
            relationship_details = []

        for relationship in relationship_details:
            if not isinstance(relationship, dict):
                continue
            tx.run(
                """
                MATCH (source:Concept {org_id: $org_id, name: $source})
                MATCH (target:Concept {org_id: $org_id, name: $target})
                MATCH (source)-[rel:RELATES_TO {collection_id: $collection_id, relation: $relation}]->(target)
                SET rel.weight = coalesce(rel.weight, 0) - $confidence
                WITH rel
                WHERE coalesce(rel.weight, 0) <= 0
                DELETE rel
                """,
                org_id=org_id,
                collection_id=collection_id,
                source=relationship.get("source") or "",
                target=relationship.get("target") or "",
                relation=relationship.get("relation") or "related_to",
                confidence=float(relationship.get("confidence") or 1.0),
            )

        cooccurrence_details = payload.get("cooccurrence_details")
        if not isinstance(cooccurrence_details, list):
            cooccurrence_details = payload.get("cooccurrences")
        if not isinstance(cooccurrence_details, list):
            cooccurrence_details = []

        for cooccurrence in cooccurrence_details:
            if not isinstance(cooccurrence, dict):
                continue
            tx.run(
                """
                MATCH (source:Concept {org_id: $org_id, name: $source})
                MATCH (target:Concept {org_id: $org_id, name: $target})
                MATCH (source)-[rel:CO_OCCURS_WITH {collection_id: $collection_id}]->(target)
                SET rel.weight = coalesce(rel.weight, 0) - 1
                WITH rel
                WHERE coalesce(rel.weight, 0) <= 0
                DELETE rel
                """,
                org_id=org_id,
                collection_id=collection_id,
                source=cooccurrence.get("source") or "",
                target=cooccurrence.get("target") or "",
            )

        tx.run(
            """
            MATCH (doc:Document {document_id: $document_id, collection_id: $collection_id})
            OPTIONAL MATCH (doc)-[:CONTAINS]->(chunk:Chunk)
            DETACH DELETE chunk
            WITH doc
            DETACH DELETE doc
            """,
            document_id=document_id,
            collection_id=collection_id,
        )
        tx.run(
            """
            MATCH (concept:Concept {org_id: $org_id})
            WHERE NOT EXISTS { MATCH (:Chunk)-[:MENTIONS]->(concept) }
            DETACH DELETE concept
            """,
            org_id=org_id,
        )
        revert_row = tx.run(
            """
            CREATE (event:ChangeEvent {
              event_id: randomUUID(),
              collection_id: $collection_id,
              org_id: $org_id,
              operation: 'revert_change',
              actor: $actor,
              timestamp: $timestamp,
              filename: $filename,
              concepts: $concepts,
              payload_json: $payload_json
            })
            WITH event
            MATCH (original:ChangeEvent {event_id: $event_id, collection_id: $collection_id, org_id: $org_id})
            MERGE (event)-[:REVERTS]->(original)
            RETURN event.event_id AS revert_event_id
            """,
            collection_id=collection_id,
            org_id=org_id,
            event_id=event_id,
            actor=actor,
            timestamp=timestamp,
            filename=event_row.get("filename") or "",
            concepts=event_row.get("concepts") or [],
            payload_json=json.dumps(
                {
                    "reverted_event_id": event_id,
                    "reverted_operation": operation,
                    "document_id": document_id,
                    "chunk_ids": chunk_ids,
                    "reason": reason,
                },
                ensure_ascii=False,
            ),
        ).single()
        return {
            "reverted": True,
            "event_id": event_id,
            "revert_event_id": (
                revert_row.get("revert_event_id") if revert_row else None
            ),
            "operation": operation,
            "document_id": document_id,
            "chunk_ids": chunk_ids,
        }

    def ingest_chunks(
        self,
        *,
        collection: Dict[str, Any],
        file_id: Optional[int],
        filename: str,
        chunks: Iterable[TextChunk],
        concepts_by_chunk: Dict[str, List[str]],
        entities: Dict[str, ExtractedEntity],
        relationships: List[ExtractedRelationship],
        actor: str = "lamb-ingestion-pipeline",
    ) -> int:
        chunk_list = list(chunks)
        if not chunk_list:
            return 0
        if not self.ensure_schema():
            return 0

        entity_map = dict(entities)
        for concept in itertools.chain.from_iterable(concepts_by_chunk.values()):
            entity_map.setdefault(
                concept,
                ExtractedEntity(
                    name=concept,
                    display_name=concept,
                    entity_type="concept",
                ),
            )
        for relationship in relationships:
            entity_map.setdefault(
                relationship.source,
                ExtractedEntity(
                    name=relationship.source,
                    display_name=relationship.source,
                    entity_type="concept",
                ),
            )
            entity_map.setdefault(
                relationship.target,
                ExtractedEntity(
                    name=relationship.target,
                    display_name=relationship.target,
                    entity_type="concept",
                ),
            )

        cooccurrences = self._cooccurrences(chunk_list, concepts_by_chunk)
        relationship_payloads = [
            relationship.__dict__
            for relationship in relationships
            if relationship.source in entity_map and relationship.target in entity_map
        ]
        entity_payloads = [entity.__dict__ for entity in entity_map.values()]

        with self.driver.session() as session:
            session.execute_write(
                self._ingest_tx,
                collection,
                int(file_id or 0),
                filename,
                chunk_list,
                concepts_by_chunk,
                sorted(entity_payloads, key=lambda item: item["name"]),
                relationship_payloads,
                sorted(cooccurrences),
                actor,
            )

        return (
            len(chunk_list)
            + len(entity_map)
            + len(relationship_payloads)
            + len(cooccurrences)
            + 1
        )

    @staticmethod
    def _ingest_tx(
        tx,
        collection: Dict[str, Any],
        file_id: int,
        filename: str,
        chunks: List[TextChunk],
        concepts_by_chunk: Dict[str, List[str]],
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        cooccurrences: List[Tuple[str, str]],
        actor: str,
    ) -> None:
        collection_id = int(collection["id"])
        org_id = str(collection.get("owner") or "default")
        document_id = f"{collection_id}:{file_id}:{filename}"
        timestamp = utc_now()

        tx.run(
            """
            MERGE (org:Organization {org_id: $org_id})
              ON CREATE SET org.created_at = $timestamp
            MERGE (collection:Collection {collection_id: $collection_id})
              ON CREATE SET collection.created_at = $timestamp
            SET collection.name = $name,
                collection.description = $description,
                collection.owner = $org_id,
                collection.collection_id = $collection_id
            MERGE (org)-[:OWNS]->(collection)
            MERGE (doc:Document {document_id: $document_id})
              ON CREATE SET doc.created_at = $timestamp
            SET doc.collection_id = $collection_id,
                doc.file_id = $file_id,
                doc.filename = $filename,
                doc.org_id = $org_id
            MERGE (collection)-[:CONTAINS]->(doc)
            """,
            org_id=org_id,
            collection_id=collection_id,
            name=collection.get("name", ""),
            description=collection.get("description") or "",
            document_id=document_id,
            file_id=file_id,
            filename=filename,
            timestamp=timestamp,
        )

        for entity in entities:
            tx.run(
                """
                MERGE (concept:Concept {org_id: $org_id, name: $name})
                  ON CREATE SET concept.created_at = $timestamp,
                                concept.sources = []
                SET concept.updated_at = $timestamp,
                    concept.collection_hint = $collection_id,
                    concept.display_name = $display_name,
                    concept.entity_type = $entity_type,
                    concept.description = $description,
                    concept.confidence = $confidence
                """,
                org_id=org_id,
                name=entity["name"],
                display_name=entity.get("display_name") or entity["name"],
                entity_type=entity.get("entity_type") or "concept",
                description=entity.get("description") or "",
                confidence=float(entity.get("confidence") or 1.0),
                collection_id=collection_id,
                timestamp=timestamp,
            )

        for chunk in chunks:
            metadata = chunk.metadata or {}
            tx.run(
                """
                MATCH (doc:Document {document_id: $document_id})
                MERGE (chunk:Chunk {chunk_id: $chunk_id})
                  ON CREATE SET chunk.created_at = $timestamp
                SET chunk.collection_id = $collection_id,
                    chunk.org_id = $org_id,
                    chunk.file_id = $file_id,
                    chunk.filename = $filename,
                    chunk.text = $text,
                    chunk.parent_text = $parent_text,
                    chunk.section_title = $section_title,
                    chunk.source_label = $source_label
                MERGE (doc)-[:CONTAINS]->(chunk)
                """,
                document_id=document_id,
                chunk_id=chunk.chunk_id,
                collection_id=collection_id,
                org_id=org_id,
                file_id=file_id,
                filename=filename,
                text=chunk.text,
                parent_text=chunk.parent_text,
                section_title=str(metadata.get("section_title") or "Document"),
                source_label=str(metadata.get("source_label") or chunk.chunk_id),
                timestamp=timestamp,
            )
            for concept in concepts_by_chunk.get(chunk.chunk_id, []):
                tx.run(
                    """
                    MATCH (chunk:Chunk {chunk_id: $chunk_id})
                    MATCH (concept:Concept {org_id: $org_id, name: $concept})
                    MERGE (chunk)-[mention:MENTIONS]->(concept)
                      ON CREATE SET mention.created_at = $timestamp
                    SET mention.collection_id = $collection_id
                    """,
                    chunk_id=chunk.chunk_id,
                    org_id=org_id,
                    concept=concept,
                    collection_id=collection_id,
                    timestamp=timestamp,
                )

        for relationship in relationships:
            tx.run(
                """
                MATCH (source:Concept {org_id: $org_id, name: $source})
                MATCH (target:Concept {org_id: $org_id, name: $target})
                MERGE (source)-[rel:RELATES_TO {collection_id: $collection_id, relation: $relation}]->(target)
                  ON CREATE SET rel.created_at = $timestamp,
                                rel.weight = 0
                SET rel.weight = coalesce(rel.weight, 0) + $confidence,
                    rel.updated_at = $timestamp,
                    rel.description = $description,
                    rel.evidence = $evidence,
                    rel.chunk_id = $chunk_id
                """,
                org_id=org_id,
                collection_id=collection_id,
                source=relationship["source"],
                target=relationship["target"],
                relation=relationship.get("relation") or "related_to",
                description=relationship.get("description") or "",
                evidence=relationship.get("evidence") or "",
                chunk_id=relationship.get("chunk_id") or "",
                confidence=float(relationship.get("confidence") or 1.0),
                timestamp=timestamp,
            )

        for concept_a, concept_b in cooccurrences:
            tx.run(
                """
                MATCH (a:Concept {org_id: $org_id, name: $concept_a})
                MATCH (b:Concept {org_id: $org_id, name: $concept_b})
                MERGE (a)-[rel:CO_OCCURS_WITH {collection_id: $collection_id}]->(b)
                  ON CREATE SET rel.created_at = $timestamp,
                                rel.weight = 0
                SET rel.weight = coalesce(rel.weight, 0) + 1,
                    rel.updated_at = $timestamp
                """,
                org_id=org_id,
                collection_id=collection_id,
                concept_a=concept_a,
                concept_b=concept_b,
                timestamp=timestamp,
            )

        tx.run(
            """
            CREATE (event:ChangeEvent {
              event_id: randomUUID(),
              collection_id: $collection_id,
              org_id: $org_id,
              operation: 'automatic_ingestion',
              actor: $actor,
              timestamp: $timestamp,
              filename: $filename,
              concepts: $concepts,
              payload_json: $payload_json
            })
            WITH event
            MATCH (doc:Document {document_id: $document_id})
            MERGE (event)-[:RECORDED_CHANGE]->(doc)
            """,
            collection_id=collection_id,
            org_id=org_id,
            actor=actor,
            timestamp=timestamp,
            filename=filename,
            concepts=[entity["name"] for entity in entities],
            payload_json=json.dumps(
                {
                    "chunks": len(chunks),
                    "chunk_ids": [chunk.chunk_id for chunk in chunks],
                    "concepts": len(entities),
                    "relationships": len(relationships),
                    "relationship_details": relationships,
                    "cooccurrences": len(cooccurrences),
                    "cooccurrence_details": [
                        {"source": source, "target": target}
                        for source, target in cooccurrences
                    ],
                },
                ensure_ascii=False,
            ),
            document_id=document_id,
        )

    def expand_from_chunks(
        self,
        collection_id: int,
        org_id: str,
        seed_chunk_ids: List[str],
        depth: int,
        limit: int,
    ) -> Dict[str, Any]:
        depth = max(1, min(int(depth or 2), 4))
        limit = max(1, int(limit or 10))
        start = time.perf_counter()
        if not seed_chunk_ids:
            return self._empty_expansion(start)
        if not self.ensure_schema():
            return self._empty_expansion(
                start,
                warning="Neo4j is not configured or available; KG expansion skipped",
            )

        with self.driver.session() as session:
            entry_rows = session.run(
                """
                MATCH (chunk:Chunk {collection_id: $collection_id})-[:MENTIONS]->(concept:Concept {org_id: $org_id})
                WHERE chunk.chunk_id IN $seed_chunk_ids
                RETURN concept.name AS name, count(*) AS mentions
                ORDER BY mentions DESC, name ASC
                LIMIT 8
                """,
                collection_id=collection_id,
                org_id=org_id,
                seed_chunk_ids=seed_chunk_ids,
            ).data()
            entry_concepts = [row["name"] for row in entry_rows]

            if not entry_concepts:
                return self._empty_expansion(start)

            relation_path_query = f"""
                MATCH (entry:Concept {{org_id: $org_id}})
                WHERE entry.name IN $entry_concepts
                MATCH path=(entry)-[:RELATES_TO*1..{depth}]-(related:Concept {{org_id: $org_id}})
                WHERE related.name <> entry.name
                  AND all(rel IN relationships(path) WHERE rel.collection_id = $collection_id)
                WITH entry, related, path,
                     length(path) AS hops,
                     reduce(score = 0.0, rel IN relationships(path) |
                         score + 2.0 * coalesce(rel.weight, 1.0)
                     ) AS path_score
                ORDER BY path_score DESC, hops ASC, related.name ASC
                LIMIT $limit
                OPTIONAL MATCH (related)<-[:MENTIONS]-(chunk:Chunk {{collection_id: $collection_id}})
                RETURN entry.name AS entry,
                       related.name AS related,
                       [rel IN relationships(path) | {{type: coalesce(rel.relation, type(rel)), raw_type: type(rel), source: startNode(rel).name, target: endNode(rel).name, weight: coalesce(rel.weight, 1), description: coalesce(rel.description, '')}}] AS edges,
                       collect(DISTINCT chunk.chunk_id)[0..6] AS chunk_ids,
                       hops AS hops,
                       path_score AS score
                ORDER BY score DESC, hops ASC
            """
            expanded_rows = session.run(
                relation_path_query,
                org_id=org_id,
                collection_id=collection_id,
                entry_concepts=entry_concepts,
                limit=limit,
            ).data()

            cooccurrence_rows = session.run(
                """
                MATCH (entry:Concept {org_id: $org_id})
                WHERE entry.name IN $entry_concepts
                MATCH path=(entry)-[:CO_OCCURS_WITH]-(related:Concept {org_id: $org_id})
                WHERE related.name <> entry.name
                  AND all(rel IN relationships(path) WHERE rel.collection_id = $collection_id)
                WITH entry, related, path,
                     1 AS hops,
                     reduce(score = 0.0, rel IN relationships(path) |
                         score + 0.25 * coalesce(rel.weight, 1.0)
                     ) AS path_score
                ORDER BY path_score DESC, related.name ASC
                LIMIT $limit
                OPTIONAL MATCH (related)<-[:MENTIONS]-(chunk:Chunk {collection_id: $collection_id})
                RETURN entry.name AS entry,
                       related.name AS related,
                       [rel IN relationships(path) | {type: coalesce(rel.relation, type(rel)), raw_type: type(rel), source: startNode(rel).name, target: endNode(rel).name, weight: coalesce(rel.weight, 1), description: coalesce(rel.description, '')}] AS edges,
                       collect(DISTINCT chunk.chunk_id)[0..3] AS chunk_ids,
                       hops AS hops,
                       path_score AS score
                ORDER BY score DESC, hops ASC
                """,
                org_id=org_id,
                collection_id=collection_id,
                entry_concepts=entry_concepts,
                limit=max(4, limit // 3),
            ).data()
            expanded_rows.extend(cooccurrence_rows)

            direct_rows = session.run(
                """
                MATCH (concept:Concept {org_id: $org_id})<-[:MENTIONS]-(chunk:Chunk {collection_id: $collection_id})
                WHERE concept.name IN $entry_concepts
                RETURN chunk.chunk_id AS chunk_id,
                       count(*) AS mentions,
                       min(chunk.source_label) AS source_label
                ORDER BY mentions DESC, source_label ASC
                LIMIT $limit
                """,
                org_id=org_id,
                collection_id=collection_id,
                entry_concepts=entry_concepts,
                limit=limit,
            ).data()

            chunk_ids: List[str] = []
            seen_chunk_ids: Set[str] = set()

            def add_chunk_id(chunk_id: str) -> None:
                if chunk_id and chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(chunk_id)
                    chunk_ids.append(chunk_id)

            for row in direct_rows:
                add_chunk_id(row.get("chunk_id"))

            edges: List[Dict[str, Any]] = []
            related_concepts: Set[str] = set(entry_concepts)
            for row in expanded_rows:
                if row.get("related"):
                    related_concepts.add(row.get("related"))
                for chunk_id in row.get("chunk_ids", []):
                    add_chunk_id(chunk_id)
                edges.extend(row.get("edges") or [])

            changes = session.run(
                """
                MATCH (event:ChangeEvent {collection_id: $collection_id, org_id: $org_id})
                WHERE any(concept IN coalesce(event.concepts, []) WHERE concept IN $concepts)
                RETURN event.operation AS operation,
                       event.actor AS actor,
                       event.timestamp AS timestamp,
                       event.filename AS filename,
                       event.concepts AS concepts,
                       event.payload_json AS payload_json
                ORDER BY event.timestamp DESC
                LIMIT 10
                """,
                collection_id=collection_id,
                org_id=org_id,
                concepts=[concept for concept in related_concepts if concept],
            ).data()

        return {
            "entry_concepts": entry_concepts,
            "expanded_chunk_ids": chunk_ids[:limit],
            "traversed_edges": self._dedupe_edges(edges),
            "latest_changes": changes,
            "graph_latency_ms": (time.perf_counter() - start) * 1000,
        }

    @staticmethod
    def _empty_expansion(start: float, warning: Optional[str] = None) -> Dict[str, Any]:
        changes: List[Dict[str, Any]] = []
        if warning:
            changes.append({"warning": warning})
        return {
            "entry_concepts": [],
            "expanded_chunk_ids": [],
            "traversed_edges": [],
            "latest_changes": changes,
            "graph_latency_ms": (time.perf_counter() - start) * 1000,
        }

    @staticmethod
    def _cooccurrences(
        chunks: List[TextChunk], concepts_by_chunk: Dict[str, List[str]]
    ) -> Set[Tuple[str, str]]:
        pairs: Set[Tuple[str, str]] = set()
        by_parent: Dict[str, Set[str]] = {}
        for chunk in chunks:
            metadata = chunk.metadata or {}
            parent_key = f"{metadata.get('filename') or metadata.get('source')}:{metadata.get('parent_chunk_id') or chunk.chunk_id}"
            by_parent.setdefault(parent_key, set()).update(
                concepts_by_chunk.get(chunk.chunk_id, [])
            )
        for concepts in by_parent.values():
            for concept_a, concept_b in itertools.combinations(sorted(concepts), 2):
                if concept_a != concept_b:
                    pairs.add((concept_a, concept_b))
        return pairs

    @staticmethod
    def _dedupe_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        deduped = []
        for edge in edges:
            key = (edge.get("source"), edge.get("target"), edge.get("type"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(edge)
        return deduped[:80]
