"""KG-RAG graph traceability endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from database.service import CollectionService
from dependencies import verify_token
from schemas.graph import (
    GraphAuditRequest,
    GraphAuditResponse,
    GraphChangeDetail,
    GraphChangeEvent,
    GraphRevertRequest,
    GraphRevertResponse,
)
from services.graph_store import get_graph_store

router = APIRouter(prefix="/graph", tags=["Graph Traceability"])


def _get_collection_or_404(db: Session, collection_id: int):
    collection = CollectionService.get_collection(db, collection_id)
    if not collection:
        raise HTTPException(
            status_code=404, detail=f"Collection {collection_id} not found"
        )
    return collection


def _graph_store_or_503():
    graph_store = get_graph_store()
    if not graph_store.is_configured():
        raise HTTPException(status_code=503, detail="KG-RAG Neo4j is not configured")
    if not graph_store.is_available():
        raise HTTPException(status_code=503, detail="KG-RAG Neo4j is not available")
    return graph_store


@router.get(
    "/collections/{collection_id}/changes",
    response_model=List[GraphChangeEvent],
    summary="List graph change history",
)
async def list_graph_changes(
    collection_id: int,
    concept: Optional[str] = Query(None, description="Filter by concept name"),
    document_id: Optional[str] = Query(None, description="Filter by graph document ID"),
    filename: Optional[str] = Query(None, description="Filter by filename"),
    operation: Optional[str] = Query(None, description="Filter by graph operation"),
    limit: int = Query(25, ge=1, le=200),
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    return graph_store.list_changes(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        concept=concept,
        document_id=document_id,
        filename=filename,
        operation=operation,
        limit=limit,
    )


@router.get(
    "/collections/{collection_id}/changes/{event_id}",
    response_model=GraphChangeDetail,
    summary="Inspect a graph change event",
)
async def get_graph_change(
    collection_id: int,
    event_id: str,
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    change = graph_store.get_change(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        event_id=event_id,
    )
    if not change:
        raise HTTPException(
            status_code=404, detail=f"Graph change {event_id} not found"
        )
    return change


@router.get(
    "/collections/{collection_id}/concepts/{concept}/changes",
    response_model=List[GraphChangeEvent],
    summary="Inspect graph changes for a concept",
)
async def list_concept_changes(
    collection_id: int,
    concept: str,
    limit: int = Query(25, ge=1, le=200),
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    return graph_store.list_changes(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        concept=concept,
        limit=limit,
    )


@router.get(
    "/collections/{collection_id}/documents/{document_id}/changes",
    response_model=List[GraphChangeEvent],
    summary="Inspect graph changes for a document",
)
async def list_document_changes(
    collection_id: int,
    document_id: str,
    limit: int = Query(25, ge=1, le=200),
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    return graph_store.list_changes(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        document_id=document_id,
        limit=limit,
    )


@router.post(
    "/collections/{collection_id}/audit-trace",
    response_model=GraphAuditResponse,
    summary="Audit a KG-RAG graph retrieval trace",
)
async def audit_graph_trace(
    collection_id: int,
    request: GraphAuditRequest,
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    trace = graph_store.expand_from_chunks(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        seed_chunk_ids=request.seed_chunk_ids,
        depth=request.graph_depth,
        limit=request.limit,
    )
    return {
        "collection_id": collection_id,
        "seed_chunk_ids": request.seed_chunk_ids,
        "trace": trace,
    }


@router.post(
    "/collections/{collection_id}/changes/{event_id}/revert",
    response_model=GraphRevertResponse,
    summary="Revert a supported graph change",
)
async def revert_graph_change(
    collection_id: int,
    event_id: str,
    request: GraphRevertRequest,
    token: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    collection = _get_collection_or_404(db, collection_id)
    graph_store = _graph_store_or_503()
    result = graph_store.revert_change(
        collection_id=collection_id,
        org_id=str(collection.get("owner")),
        event_id=event_id,
        actor=request.actor,
        reason=request.reason,
    )
    if not result.get("reverted") and result.get("reason") == "change_not_found":
        raise HTTPException(
            status_code=404, detail=f"Graph change {event_id} not found"
        )
    if not result.get("reverted"):
        raise HTTPException(status_code=400, detail=result)
    return result
