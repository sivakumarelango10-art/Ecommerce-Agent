import os
import datetime
import hashlib
import hmac
import logging
import urllib.parse
from typing import Any
import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.core.config import Settings

logger = logging.getLogger(__name__)


class NeonSigV4Client:
    """Zero-dependency AWS SigV4 client for Neon Object Storage with RFC 3986 encoding and connection reuse."""

    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-2",
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region
        self.parsed_url = urllib.parse.urlparse(self.endpoint_url)
        self.host = self.parsed_url.netloc
        self._client: httpx.Client | None = None

    def __repr__(self) -> str:
        return f"<NeonSigV4Client endpoint={self.endpoint_url!r} host={self.host!r}>"

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=10.0)
        return self._client

    def close(self) -> None:
        """Close persistent HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None

    def __enter__(self) -> "NeonSigV4Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _sign(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _get_signature_key(self, date_stamp: str) -> bytes:
        k_date = self._sign(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp)
        k_region = self._sign(k_date, self.region)
        k_service = self._sign(k_region, "s3")
        return self._sign(k_service, "aws4_request")

    def _canonical_uri(self, bucket: str, key: str) -> str:
        """RFC 3986 URI path encoding for S3 SigV4 Canonical Request."""
        clean_bucket = urllib.parse.quote(bucket.strip("/"), safe="-_.~")
        clean_key = key.lstrip("/")
        encoded_segments = [urllib.parse.quote(seg, safe="-_.~") for seg in clean_key.split("/")]
        return f"/{clean_bucket}/" + "/".join(encoded_segments)

    def put_object(self, bucket: str, key: str, data: bytes, content_type: str = "text/plain") -> httpx.Response:
        canonical_path = self._canonical_uri(bucket, key)
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(data).hexdigest()
        canonical_headers = f"host:{self.host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = f"PUT\n{canonical_path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

        k_signing = self._get_signature_key(date_stamp)
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        auth_header = f"{algorithm} Credential={self.access_key_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

        headers = {
            "host": self.host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": auth_header,
            "Content-Type": content_type,
        }

        url = f"{self.endpoint_url}{canonical_path}"
        return self._get_client().put(url, headers=headers, content=data)

    def get_object(self, bucket: str, key: str) -> httpx.Response:
        canonical_path = self._canonical_uri(bucket, key)
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_headers = f"host:{self.host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = f"GET\n{canonical_path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

        k_signing = self._get_signature_key(date_stamp)
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        auth_header = f"{algorithm} Credential={self.access_key_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

        headers = {
            "host": self.host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": auth_header,
        }

        url = f"{self.endpoint_url}{canonical_path}"
        return self._get_client().get(url, headers=headers)


class NeonService:
    """Unified service for Neon backend primitives."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._storage_client: NeonSigV4Client | None = None
        if (
            settings.aws_endpoint_url_s3
            and settings.aws_access_key_id
            and settings.aws_secret_access_key
        ):
            self._storage_client = NeonSigV4Client(
                endpoint_url=settings.aws_endpoint_url_s3,
                access_key_id=settings.aws_access_key_id.get_secret_value(),
                secret_access_key=settings.aws_secret_access_key.get_secret_value(),
                region=settings.aws_region or "us-east-2",
            )

    def close(self) -> None:
        """Close persistent storage client session."""
        if self._storage_client:
            self._storage_client.close()

    def check_postgres(self, db: Session) -> dict[str, Any]:
        """Verify Postgres connection against Neon Lakebase Postgres."""
        result = db.execute(text("SELECT 1 as alive, current_database() as db, version() as ver")).mappings().first()
        return {
            "status": "connected" if result and result["alive"] == 1 else "disconnected",
            "database": result["db"] if result else None,
            "version": (result["ver"][:40] + "...") if result and result["ver"] else None,
            "pooled": "-pooler" in self.settings.database_url.get_secret_value(),
        }

    def check_functions(self) -> dict[str, Any]:
        """Invoke the Neon Function `hello`."""
        branch_id = self.settings.neon_branch_id
        suffix = self.settings.neon_function_host_suffix
        if not branch_id or not suffix:
            return {"status": "not_configured"}

        func_url = f"https://{branch_id}-hello{suffix}"
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(func_url)
                return {
                    "status": "available" if res.status_code == 200 else "error",
                    "status_code": res.status_code,
                    "url": func_url,
                    "response": res.text.strip(),
                }
        except Exception as exc:
            logger.error("check_functions failed: %s", type(exc).__name__)
            return {"status": "error", "error": "Function check failed"}

    def check_object_storage(self) -> dict[str, Any]:
        """Verify Neon S3-compatible Object Storage."""
        if not self._storage_client:
            return {"status": "not_configured"}

        test_key = "system/status_check.txt"
        test_payload = b"Neon Object Storage Status OK"
        try:
            put_res = self._storage_client.put_object("assets", test_key, test_payload)
            if put_res.status_code != 200:
                logger.error("check_object_storage PUT failed with status %d", put_res.status_code)
                return {
                    "status": "error",
                    "bucket": "assets",
                    "error": "Storage PUT failed",
                }

            get_res = self._storage_client.get_object("assets", test_key)
            if get_res.status_code != 200 or get_res.content != test_payload:
                logger.error("check_object_storage GET failed with status %d", get_res.status_code)
                return {
                    "status": "error",
                    "bucket": "assets",
                    "error": "Storage GET failed",
                }

            return {
                "status": "available",
                "bucket": "assets",
                "endpoint": self.settings.aws_endpoint_url_s3,
                "region": self.settings.aws_region or "us-east-2",
            }
        except Exception as exc:
            logger.error("check_object_storage failed: %s", type(exc).__name__)
            return {"status": "error", "error": "Object storage check failed"}

    def check_ai_gateway(self) -> dict[str, Any]:
        """Verify Neon AI Gateway models endpoint."""
        base_url = self.settings.neon_ai_gateway_base_url
        token = self.settings.neon_ai_gateway_token
        if not base_url or not token:
            return {"status": "not_configured"}

        url = f"{base_url.rstrip('/')}/v1/models"
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"}
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    models = data.get("data", [])
                    return {
                        "status": "available",
                        "endpoint": url,
                        "models_count": len(models),
                        "notice": "Gateway live and authenticating. Model inference requires adding card/credits in Neon billing.",
                    }
                logger.error("check_ai_gateway returned status %d", res.status_code)
                return {"status": "error", "status_code": res.status_code}
        except Exception as exc:
            logger.error("check_ai_gateway failed: %s", type(exc).__name__)
            return {"status": "error", "error": "AI Gateway check failed"}

    def check_auth(self) -> dict[str, Any]:
        """Verify Neon Auth (Managed Better Auth) JWKS endpoint."""
        jwks_url = self.settings.neon_auth_jwks_url
        base_url = self.settings.neon_auth_base_url
        if not jwks_url:
            return {"status": "not_configured"}

        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(jwks_url)
                if res.status_code == 200:
                    keys = res.json().get("keys", [])
                    return {
                        "status": "available",
                        "jwks_url": jwks_url,
                        "base_url": base_url,
                        "jwks_keys_count": len(keys),
                    }
                logger.error("check_auth returned status %d", res.status_code)
                return {"status": "error", "status_code": res.status_code}
        except Exception as exc:
            logger.error("check_auth failed: %s", type(exc).__name__)
            return {"status": "error", "error": "Auth check failed"}

    def check_realtime(self) -> dict[str, Any]:
        """Verify Neon Realtime configuration."""
        rt_url = self.settings.neon_realtime_url
        rt_secret = self.settings.neon_realtime_secret
        if not rt_url or not rt_secret:
            return {"status": "not_configured"}

        # Probe the HTTP/WebSocket endpoint
        http_probe = rt_url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(http_probe)
                # 400 Bad Request is the expected response from a WebSocket endpoint when accessed via HTTP
                is_up = res.status_code in {200, 400, 426}
                return {
                    "status": "available" if is_up else "error",
                    "websocket_url": rt_url,
                    "secret_configured": bool(rt_secret.get_secret_value()),
                    "probe_status_code": res.status_code,
                }
        except Exception as exc:
            logger.error("check_realtime probe failed: %s", type(exc).__name__)
            return {
                "status": "configured",
                "websocket_url": rt_url,
                "secret_configured": bool(rt_secret.get_secret_value()),
                "probe_note": "Realtime connectivity probe failed.",
            }

    def get_all_status(self, db: Session) -> dict[str, Any]:
        """Check all 6 Neon services end-to-end."""
        return {
            "branch": self.settings.neon_branch or "production",
            "branch_id": self.settings.neon_branch_id,
            "services": {
                "postgres": self.check_postgres(db),
                "functions": self.check_functions(),
                "object_storage": self.check_object_storage(),
                "ai_gateway": self.check_ai_gateway(),
                "auth": self.check_auth(),
                "realtime": self.check_realtime(),
            },
        }
