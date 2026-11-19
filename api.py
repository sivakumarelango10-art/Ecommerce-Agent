"""Backward-compatible import for ``uvicorn api:app``."""

from backend.app.main import app

__all__ = ["app"]
