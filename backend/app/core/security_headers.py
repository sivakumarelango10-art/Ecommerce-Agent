from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware injecting hardened HTTP security headers into every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Standard defense-in-depth headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Explicitly enforce UTF-8 charset on JSON responses to prevent client/proxy decoding to ISO-8859-1
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type and "charset" not in content_type:
            response.headers["content-type"] = f"{content_type}; charset=utf-8"

        # Content-Security-Policy
        # Configured to permit Vite/React development assets and API calls while strictly forbidding frame injection and plugins
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )

        # Strict-Transport-Security: Enforce when in production or on HTTPS connections
        settings = getattr(request.app.state, "settings", None)
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        )
        is_production = settings and getattr(settings, "environment", "") == "production"

        if is_production or is_https:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response
