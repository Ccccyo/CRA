"""Vercel Python entrypoint for the CRA Agent FastAPI application."""

from pathlib import Path
import sys

from fastapi import Request


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from cra_api.main import app  # noqa: E402


@app.middleware("http")
async def restore_vercel_route(request: Request, call_next):
    """Restore the public path captured by the Vercel catch-all route."""
    forwarded_path = request.query_params.get("__cra_path")
    if forwarded_path is not None:
        normalized = f"/{forwarded_path.lstrip('/')}"
        request.scope["path"] = normalized
        request.scope["raw_path"] = normalized.encode("utf-8")
    return await call_next(request)
