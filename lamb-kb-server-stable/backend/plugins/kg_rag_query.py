"""KG-RAG query plugin for vector seed retrieval plus graph expansion."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import time

import config as config_module
from plugins.base import PluginRegistry, QueryPlugin
from services.graph_store import get_graph_store


@PluginRegistry.register
class KGRAGQueryPlugin(QueryPlugin):
    """Query plugin that augments vector retrieval with Neo4j graph expansion."""

    name = "kg_rag_query"
    description = "KG-RAG query with vector seed retrieval and graph expansion"

    def get_parameters(self) -> Dict[str, Dict[str, Any]]:
        return {
            "top_k": {
                "type": "integer",
                "description": "Number of vector seed results to retrieve",
                "required": False,
                "default": 5,
            },
            "threshold": {
                "type": "number",
                "description": "Minimum vector similarity threshold (0-1)",
                "required": False,
                "default": 0.0,
            },
            "graph_depth": {
                "type": "integer",
                "description": "Maximum RELATES_TO graph traversal depth (1-4)",
                "required": False,
                "default": 2,
            },
            "graph_limit_factor": {
                "type": "integer",
                "description": "Expansion limit multiplier based on top_k",
                "required": False,
                "default": 4,
            },
            "return_parent_context": {
                "type": "boolean",
                "description": "Return parent chunk text when available",
                "required": False,
                "default": True,
            },
            "include_trace": {
                "type": "boolean",
                "description": "Attach KG-RAG trace metadata to returned results",
                "required": False,
                "default": True,
            },
        }

    def query(
        self, collection_id: int, query_text: str, **kwargs
    ) -> List[Dict[str, Any]]:
        top_k = int(kwargs.get("top_k", 5) or 5)
        threshold = float(kwargs.get("threshold", 0.0) or 0.0)
        return_parent_context = self._as_bool(kwargs.get("return_parent_context", True))
        include_trace = self._as_bool(kwargs.get("include_trace", True))
        db = kwargs.get("db")
        chroma_collection = kwargs.get("chroma_collection")

        if not db:
            raise ValueError("Database session is required")
        if not chroma_collection:
            raise ValueError("ChromaDB collection is required")

        kg_config = config_module.get_kg_rag_config()
        graph_depth = int(
            kwargs.get("graph_depth") or kg_config.get("graph_depth") or 2
        )
        graph_depth = max(1, min(graph_depth, 4))
        graph_limit_factor = int(
            kwargs.get("graph_limit_factor") or kg_config.get("limit_factor") or 4
        )
        graph_limit_factor = max(1, min(graph_limit_factor, 20))

        vector_start = time.perf_counter()
        baseline_results = self._query_vector_baseline(
            chroma_collection=chroma_collection,
            query_text=query_text,
            top_k=top_k,
            threshold=threshold,
        )
        vector_ms = (time.perf_counter() - vector_start) * 1000

        seed_chunk_ids = [
            chunk_id
            for chunk_id in (
                self._result_chunk_id(result) for result in baseline_results
            )
            if chunk_id
        ]

        trace = {
            "mode": "kg_rag",
            "enabled": bool(kg_config.get("enabled")),
            "graph_expanded": False,
            "seed_chunk_ids": seed_chunk_ids,
            "entry_concepts": [],
            "traversed_edges": [],
            "expanded_chunk_ids": [],
            "latest_changes": [],
            "vector_latency_ms": vector_ms,
            "graph_latency_ms": 0.0,
            "warnings": [],
        }

        if not kg_config.get("enabled"):
            trace["warnings"].append("KG-RAG is disabled; returning vector baseline")
            return self._attach_trace(baseline_results, trace, include_trace)
        if not seed_chunk_ids:
            trace["warnings"].append(
                "No vector seed chunks found; graph expansion skipped"
            )
            return self._attach_trace(baseline_results, trace, include_trace)

        from database.service import CollectionService

        collection = CollectionService.get_collection(db, collection_id)
        if not collection:
            raise ValueError(f"Collection with ID {collection_id} not found")
        org_id = str(
            collection.get("owner")
            if isinstance(collection, dict)
            else collection.owner
        )

        graph_store = get_graph_store()
        if not graph_store.is_configured():
            trace["warnings"].append(
                "Neo4j is not configured; returning vector baseline"
            )
            return self._attach_trace(baseline_results, trace, include_trace)

        expansion = graph_store.expand_from_chunks(
            collection_id=collection_id,
            org_id=org_id,
            seed_chunk_ids=seed_chunk_ids,
            depth=graph_depth,
            limit=top_k * graph_limit_factor,
        )
        trace.update(
            {
                "graph_expanded": bool(expansion.get("expanded_chunk_ids")),
                "entry_concepts": expansion.get("entry_concepts", []),
                "traversed_edges": expansion.get("traversed_edges", []),
                "expanded_chunk_ids": expansion.get("expanded_chunk_ids", []),
                "latest_changes": expansion.get("latest_changes", []),
                "graph_latency_ms": expansion.get("graph_latency_ms", 0.0),
            }
        )

        expanded_ids = [
            chunk_id
            for chunk_id in expansion.get("expanded_chunk_ids", [])
            if chunk_id not in seed_chunk_ids
        ]
        expanded_results = self._fetch_expanded_results(
            chroma_collection=chroma_collection,
            expanded_ids=expanded_ids,
            return_parent_context=return_parent_context,
        )

        merged = self._merge_results(baseline_results + expanded_results, top_k=top_k)
        if not expanded_results and not trace["graph_expanded"]:
            trace["warnings"].append("Graph returned no additional chunks")
        return self._attach_trace(merged, trace, include_trace)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enable",
            "enabled",
        }

    @staticmethod
    def _query_vector_baseline(
        chroma_collection: Any,
        query_text: str,
        top_k: int,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        results = chroma_collection.query(
            query_texts=[query_text],
            n_results=top_k,
        )

        formatted_results: List[Dict[str, Any]] = []
        if results and len(results.get("documents", [])) > 0:
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            for index, document in enumerate(documents):
                if index >= len(metadatas) or index >= len(distances):
                    continue
                similarity = 1.0 - distances[index]
                if similarity >= threshold:
                    formatted_results.append(
                        {
                            "similarity": similarity,
                            "data": document,
                            "metadata": metadatas[index],
                        }
                    )
        return formatted_results

    @staticmethod
    def _result_chunk_id(result: Dict[str, Any]) -> Optional[str]:
        metadata = result.get("metadata") or {}
        return (
            metadata.get("document_id")
            or metadata.get("child_chunk_id")
            or metadata.get("chunk_id")
        )

    def _fetch_expanded_results(
        self,
        chroma_collection: Any,
        expanded_ids: List[str],
        return_parent_context: bool,
    ) -> List[Dict[str, Any]]:
        if not expanded_ids:
            return []
        try:
            rows = chroma_collection.get(
                ids=expanded_ids,
                include=["documents", "metadatas"],
            )
        except Exception:
            return []

        ids = rows.get("ids") or expanded_ids
        documents = rows.get("documents") or []
        metadatas = rows.get("metadatas") or []
        results: List[Dict[str, Any]] = []
        for index, chunk_id in enumerate(ids):
            document = documents[index] if index < len(documents) else ""
            metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
            metadata.setdefault("document_id", chunk_id)
            metadata["kg_rag_origin"] = "graph_expansion"
            data = metadata.get("parent_text") if return_parent_context else None
            results.append(
                {
                    "similarity": 0.72,
                    "data": data or document,
                    "metadata": metadata,
                }
            )
        return results

    def _merge_results(
        self, results: List[Dict[str, Any]], top_k: int
    ) -> List[Dict[str, Any]]:
        by_chunk_id: Dict[str, Dict[str, Any]] = {}
        for result in results:
            chunk_id = self._result_chunk_id(result) or result.get("data", "")[:120]
            existing = by_chunk_id.get(chunk_id)
            if existing is None or float(result.get("similarity", 0.0)) > float(
                existing.get("similarity", 0.0)
            ):
                by_chunk_id[chunk_id] = result

        ordered = sorted(
            by_chunk_id.values(),
            key=lambda item: float(item.get("similarity", 0.0)),
            reverse=True,
        )
        return ordered[: max(top_k, 1) + 3]

    @staticmethod
    def _attach_trace(
        results: List[Dict[str, Any]],
        trace: Dict[str, Any],
        include_trace: bool,
    ) -> List[Dict[str, Any]]:
        if not include_trace:
            return results
        traced_results: List[Dict[str, Any]] = []
        for result in results:
            item = dict(result)
            metadata = dict(item.get("metadata") or {})
            metadata["kg_rag"] = trace
            item["metadata"] = metadata
            traced_results.append(item)
        return traced_results


kg_rag_query_plugin = KGRAGQueryPlugin()
