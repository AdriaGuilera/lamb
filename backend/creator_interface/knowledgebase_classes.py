from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union


class KnowledgeBaseMetadata(BaseModel):
    description: Optional[str] = ""
    access_control: Optional[str] = "private"  # private or public


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""
    access_control: str = "private"  # private or public
    graph_enabled: bool = False
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    access_control: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeBaseQuery(BaseModel):
    query_text: str
    plugin_name: str = "simple_query"
    plugin_params: Dict[str, Any] = {}


class KnowledgeBaseFile(BaseModel):
    id: str
    filename: str
    size: int
    content_type: str
    created_at: int


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    files: List[KnowledgeBaseFile] = []
    owner: Optional[str] = None
    created_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeBaseListResponse(BaseModel):
    knowledge_bases: List[Dict[str, Any]] 