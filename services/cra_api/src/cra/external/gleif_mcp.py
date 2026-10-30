"""A bounded, read-only MCP tool for exact GLEIF LEI record lookup.

The MCP boundary is for reusable external-reference access, not credit decisions.
No applicant name, BP, financial data or credentials are sent to GLEIF.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx


LEI_PATTERN = re.compile(r"^[A-Z0-9]{20}$")
GLEIF_API = "https://api.gleif.org/api/v1/lei-records"
Fetch = Callable[[str], Awaitable[dict[str, Any]]]


def normalize_lei(value: str) -> str:
    lei = value.strip().upper()
    if not LEI_PATTERN.fullmatch(lei):
        raise ValueError("LEI must contain exactly 20 ASCII letters or digits")
    return lei


def requested_lei(question: str) -> Optional[str]:
    """Route only an explicit, single LEI lookup; never fuzzy-search a client name."""
    if not re.search(r"\bLEI\b|法人识别码|法律实体识别码", question, re.I):
        return None
    candidates = set(re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9]{20}(?![A-Za-z0-9])", question))
    if len(candidates) != 1:
        return None
    return normalize_lei(next(iter(candidates)))


async def fetch_gleif_record(lei: str) -> dict[str, Any]:
    url = f"{GLEIF_API}/{normalize_lei(lei)}"
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "application/vnd.api+json"})
    if response.status_code == 404:
        return {"data": None}
    response.raise_for_status()
    if len(response.content) > 200_000:
        raise ValueError("GLEIF response exceeds the configured size limit")
    return response.json()


def _record(payload: dict[str, Any], lei: str) -> dict[str, Any]:
    source_url = f"{GLEIF_API}/{lei}"
    observed_at = datetime.now(timezone.utc).isoformat()
    data = payload.get("data")
    if not isinstance(data, dict):
        return {
            "status": "not_found", "lei": lei, "source_url": source_url,
            "observed_at": observed_at,
        }
    if str(data.get("id", "")).upper() != lei:
        raise ValueError("GLEIF response LEI does not match the requested entity")
    attributes = data.get("attributes") or {}
    entity = attributes.get("entity") or {}
    registration = attributes.get("registration") or {}
    legal_name = entity.get("legalName") or {}
    address = entity.get("legalAddress") or {}
    return {
        "status": "found",
        "lei": lei,
        "legal_name": legal_name.get("name") if isinstance(legal_name, dict) else None,
        "legal_address_country": address.get("country") if isinstance(address, dict) else None,
        "registration_status": registration.get("status"),
        "last_update_date": registration.get("lastUpdateDate"),
        "source_url": source_url,
        "observed_at": observed_at,
        "use_limit": "Entity reference only; not SAP BP matching, credit rating, or approval.",
    }


def create_gleif_server(fetch: Optional[Fetch] = None):
    """Official MCP SDK server; in-memory transport for the first safe pilot."""
    from mcp.server import MCPServer

    server = MCPServer("cra-gleif-public-reference")
    fetcher = fetch or fetch_gleif_record

    @server.tool()
    async def lookup_lei(lei: str) -> dict[str, Any]:
        """Read one exact public GLEIF LEI record; no search or writes."""
        normalized = normalize_lei(lei)
        payload = await fetcher(normalized)
        return _record(payload, normalized)

    return server


async def lookup_lei_via_mcp(lei: str, server=None) -> dict[str, Any]:
    """MCP client boundary: never pass raw unvalidated tool text into the model."""
    from mcp import Client

    normalized = normalize_lei(lei)
    async with Client(server or create_gleif_server()) as client:
        result = await client.call_tool("lookup_lei", {"lei": normalized})
    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError("GLEIF MCP lookup failed")
    content = result.structured_content
    if "result" in content and isinstance(content["result"], dict):
        content = content["result"]
    if content.get("lei") != normalized or content.get("status") not in {"found", "not_found"}:
        raise RuntimeError("GLEIF MCP returned an invalid record")
    return content
