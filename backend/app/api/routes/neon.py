from collections.abc import Iterator
import logging
import re
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.auth_dependencies import get_current_admin
from backend.app.core.config import Settings
from backend.app.db.session import get_db_session
from backend.app.services.neon_service import NeonService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/neon",
    tags=["neon"],
    dependencies=[Depends(get_current_admin)],
)

OBJECT_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\./]+$")
DEFAULT_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/octet-stream",
})


def validate_object_key(key: str) -> str:
    """Validate object key syntax for upload and download without silent mutations."""
    if not isinstance(key, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key must be a valid string.",
        )
    if len(key) < 1 or len(key) > 256:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key length must be between 1 and 256 characters.",
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key contains invalid control characters.",
        )
    if key.startswith("/") or key.endswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key must not start or end with a slash.",
        )
    if "//" in key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key must not contain consecutive slashes.",
        )
    segments = key.split("/")
    for segment in segments:
        if segment in {"..", "."} or segment == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Object key contains invalid traversal segments.",
            )
    if not OBJECT_KEY_PATTERN.match(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Object key contains unsupported characters.",
        )
    return key


def check_upload_size_limit(request: Request) -> None:
    """Enforce request payload size ceiling before reading/buffering full request body."""
    settings = getattr(request.app.state, "settings", None)
    max_bytes = getattr(settings, "storage_max_upload_bytes", 2 * 1024 * 1024) if settings else 2 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length_int = int(content_length)
            if length_int > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"Request payload ({length_int} bytes) exceeds maximum permitted size of {max_bytes} bytes.",
                )
        except ValueError:
            pass


def get_neon_service(request: Request) -> Iterator[NeonService]:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from backend.app.core.config import load_settings
        settings = load_settings()
    service = NeonService(settings)
    try:
        yield service
    finally:
        service.close()


class StorageUploadRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., min_length=0, max_length=2 * 1024 * 1024)
    content_type: str = Field(default="text/plain", max_length=128)


@router.get("/status", summary="Check Neon cloud services health")
def get_neon_status(
    db: Session = Depends(get_db_session),
    neon: NeonService = Depends(get_neon_service),
) -> dict[str, Any]:
    """Inspect and report the health of all six wired Neon services."""
    return neon.get_all_status(db)


@router.get("/function/hello", summary="Invoke Neon hello function")
def invoke_hello_function(
    neon: NeonService = Depends(get_neon_service),
) -> dict[str, Any]:
    """Invoke the deployed Neon Function `hello`."""
    res = neon.check_functions()
    if res.get("status") == "error":
        logger.error("Neon function invocation failed: %s", res.get("error"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream function service error.",
        )
    return res


@router.post(
    "/storage/upload",
    summary="Upload object to Neon Object Storage",
    dependencies=[Depends(check_upload_size_limit)],
)
def upload_object(
    request: Request,
    payload: StorageUploadRequest,
    neon: NeonService = Depends(get_neon_service),
) -> dict[str, Any]:
    """Store an object in Neon Object Storage bucket `assets`."""
    clean_key = validate_object_key(payload.key)

    settings = getattr(neon, "settings", None) or getattr(request.app.state, "settings", None)
    max_bytes = getattr(settings, "storage_max_upload_bytes", 2 * 1024 * 1024) if settings else 2 * 1024 * 1024

    raw_bytes = payload.content.encode("utf-8")
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload content ({len(raw_bytes)} bytes) exceeds maximum permitted size of {max_bytes} bytes.",
        )

    clean_content_type = payload.content_type.strip().lower()
    allowed_types = getattr(
        settings,
        "storage_allowed_content_types",
        DEFAULT_ALLOWED_CONTENT_TYPES,
    ) if settings else DEFAULT_ALLOWED_CONTENT_TYPES
    if clean_content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content-Type '{payload.content_type}' is not supported.",
        )

    if not neon._storage_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neon Object Storage is not configured.",
        )
    res = neon._storage_client.put_object(
        bucket="assets",
        key=clean_key,
        data=raw_bytes,
        content_type=clean_content_type,
    )
    if res.status_code != 200:
        logger.error("S3 PutObject failed: status=%d, key=%s", res.status_code, clean_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream object storage service error.",
        )
    return {
        "status": "success",
        "bucket": "assets",
        "key": clean_key,
        "bytes_stored": len(raw_bytes),
    }


@router.get("/storage/download", summary="Download object from Neon Object Storage")
def download_object(
    key: str = Query(..., min_length=1),
    neon: NeonService = Depends(get_neon_service),
) -> dict[str, Any]:
    """Retrieve an object from Neon Object Storage bucket `assets`."""
    clean_key = validate_object_key(key)

    if not neon._storage_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neon Object Storage is not configured.",
        )
    res = neon._storage_client.get_object(bucket="assets", key=clean_key)
    if res.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Object '{clean_key}' was not found.",
        )
    elif res.status_code != 200:
        logger.error("S3 GetObject failed: status=%d, key=%s", res.status_code, clean_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream object storage service error.",
        )
    return {
        "status": "success",
        "bucket": "assets",
        "key": clean_key,
        "content": res.text,
        "content_type": res.headers.get("content-type", "application/octet-stream"),
    }


@router.get("/ai-gateway/models", summary="List Neon AI Gateway models")
def list_ai_gateway_models(
    neon: NeonService = Depends(get_neon_service),
) -> dict[str, Any]:
    """Retrieve live catalog models from Neon AI Gateway."""
    res = neon.check_ai_gateway()
    if res.get("status") == "error":
        logger.error("Neon AI Gateway invocation failed: %s", res.get("error"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream AI Gateway service error.",
        )
    return res
