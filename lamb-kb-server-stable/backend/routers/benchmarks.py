"""Benchmark endpoints for vector RAG versus KG-RAG evaluation."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.connection import get_db
from dependencies import verify_token
from schemas.benchmark import (
    BenchmarkDataset,
    BenchmarkDatasetSummary,
    BenchmarkRunAllRequest,
    BenchmarkRunAllResponse,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
)
from services.benchmark import BenchmarkService


router = APIRouter(
    prefix="/benchmarks",
    tags=["Benchmarks"],
    dependencies=[Depends(verify_token)],
)


@router.get(
    "/datasets",
    response_model=List[BenchmarkDatasetSummary],
    summary="List built-in benchmark datasets",
)
async def list_benchmark_datasets() -> List[BenchmarkDatasetSummary]:
    return BenchmarkService.list_datasets()


@router.get(
    "/datasets/{dataset_id}",
    response_model=BenchmarkDataset,
    summary="Get a built-in benchmark dataset",
)
async def get_benchmark_dataset(dataset_id: str) -> BenchmarkDataset:
    return BenchmarkService.get_dataset(dataset_id)


@router.post(
    "/collections/{collection_id}/run",
    response_model=BenchmarkRunResponse,
    summary="Run a benchmark dataset against a collection",
)
async def run_collection_benchmark(
    collection_id: int,
    request: BenchmarkRunRequest,
    db: Session = Depends(get_db),
) -> BenchmarkRunResponse:
    return BenchmarkService.run(db=db, collection_id=collection_id, request=request)


@router.post(
    "/collections/{collection_id}/run-all",
    response_model=BenchmarkRunAllResponse,
    summary="Run all selected benchmark datasets against a collection",
)
async def run_all_collection_benchmarks(
    collection_id: int,
    request: BenchmarkRunAllRequest,
    db: Session = Depends(get_db),
) -> BenchmarkRunAllResponse:
    return BenchmarkService.run_all(
        db=db,
        collection_id=collection_id,
        dataset_ids=request.dataset_ids,
        threshold=request.threshold,
    )
