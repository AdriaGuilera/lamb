"""Schemas for KG-RAG graph traceability endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GraphChangeEvent(BaseModel):
    event_id: str = Field(..., description="Unique graph change event ID")
    collection_id: int = Field(..., description="Collection ID")
    org_id: str = Field(..., description="Organization or owner scope")
    operation: str = Field(..., description="Graph operation name")
    actor: Optional[str] = Field(None, description="Actor that produced the change")
    timestamp: Optional[str] = Field(None, description="Change timestamp")
    filename: Optional[str] = Field(None, description="Source filename")
    concepts: List[str] = Field(default_factory=list, description="Concepts touched by the change")
    payload_json: Optional[str] = Field(None, description="Raw JSON payload stored in Neo4j")
    document_id: Optional[str] = Field(None, description="Related graph document ID")
    file_id: Optional[int] = Field(None, description="Related file registry ID")


class GraphChangeDetail(GraphChangeEvent):
    chunk_ids: List[str] = Field(default_factory=list, description="Related graph chunk IDs")


class GraphAuditRequest(BaseModel):
    seed_chunk_ids: List[str] = Field(..., description="Chroma/graph chunk IDs to use as graph entry points")
    graph_depth: int = Field(2, description="RELATES_TO traversal depth")
    limit: int = Field(20, description="Maximum number of expanded chunks to return")


class GraphRevertRequest(BaseModel):
    actor: str = Field("graph-traceability-api", description="Actor requesting the revert")
    reason: str = Field("", description="Human-readable reason for the revert")


class GraphRevertResponse(BaseModel):
    reverted: bool = Field(..., description="Whether the revert was applied")
    reason: Optional[str] = Field(None, description="Reason when no revert was applied")
    event_id: Optional[str] = Field(None, description="Requested event ID")
    revert_event_id: Optional[str] = Field(None, description="Audit event created for the revert")
    operation: Optional[str] = Field(None, description="Original operation")
    document_id: Optional[str] = Field(None, description="Reverted graph document ID")
    chunk_ids: List[str] = Field(default_factory=list, description="Reverted graph chunk IDs")


class GraphAuditResponse(BaseModel):
    collection_id: int = Field(..., description="Collection ID")
    seed_chunk_ids: List[str] = Field(..., description="Requested seed chunk IDs")
    trace: Dict[str, Any] = Field(..., description="Graph expansion trace")
