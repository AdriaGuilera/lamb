import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from lamb.database_manager import LambDatabaseManager
from lamb.logging_config import get_logger

from .kb_server_manager import KBServerManager
from .knowledges_router import authenticate_creator_user

logger = get_logger(__name__, component="KG_RAG_PROXY")

router = APIRouter(tags=["Graph RAG Proxy"])
kb_server_manager = KBServerManager()
db_manager = LambDatabaseManager()


def _collection_id_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "collections":
        return parts[1]
    return None


def _is_graph_write(method: str, path: str) -> bool:
    if method.upper() == "PATCH":
        return True
    return (
        "concepts/merge" in path
        or path.endswith("/revert")
        or path.endswith("/migrate")
    )


def _require_collection_access(
    collection_id: str, creator_user: dict, *, write: bool = False
) -> None:
    can_access, access_type = db_manager.user_can_access_kb(
        collection_id, creator_user["id"]
    )
    if not can_access:
        raise HTTPException(status_code=404, detail="KB not found or not accessible")
    if write and access_type != "owner":
        raise HTTPException(
            status_code=403, detail="Only the KB owner can modify graph curation data"
        )


async def _proxy_to_kb(request: Request, creator_user: dict, kb_path: str) -> Response:
    try:
        kb_config = kb_server_manager._get_kb_config_for_user(creator_user)
    except Exception as exc:
        logger.warning("Could not resolve KB server config: %s", exc)
        raise HTTPException(
            status_code=503, detail="Knowledge Base server is not configured"
        ) from exc

    kb_server_url = kb_config.get("url")
    kb_token = kb_config.get("token")
    if not kb_server_url or not kb_token:
        raise HTTPException(
            status_code=503, detail="Knowledge Base server is not configured"
        )

    target_url = f"{kb_server_url.rstrip('/')}/{kb_path.lstrip('/')}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = kb_server_manager._get_auth_headers(kb_token)
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type

    body = await request.body()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0)
        ) as client:
            kb_response = await client.request(
                request.method,
                target_url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as exc:
        logger.warning("KB proxy request failed for %s: %s", kb_path, exc)
        raise HTTPException(
            status_code=503, detail=f"Unable to connect to KB server: {exc}"
        ) from exc

    response_headers = {}
    response_content_type = kb_response.headers.get("content-type")
    if response_content_type:
        response_headers["content-type"] = response_content_type

    return Response(
        content=kb_response.content,
        status_code=kb_response.status_code,
        headers=response_headers,
    )


@router.api_route("/graph/{path:path}", methods=["GET", "POST", "PATCH"])
async def proxy_graph_request(path: str, request: Request) -> Response:
    creator_user = await authenticate_creator_user(request)
    collection_id = _collection_id_from_path(path)
    if collection_id:
        _require_collection_access(
            collection_id,
            creator_user,
            write=_is_graph_write(request.method, path),
        )
    return await _proxy_to_kb(request, creator_user, f"/graph/{path}")


@router.api_route("/benchmarks/{path:path}", methods=["GET", "POST"])
async def proxy_benchmark_request(path: str, request: Request) -> Response:
    creator_user = await authenticate_creator_user(request)
    collection_id = _collection_id_from_path(path)
    if collection_id:
        _require_collection_access(collection_id, creator_user)
    return await _proxy_to_kb(request, creator_user, f"/benchmarks/{path}")
