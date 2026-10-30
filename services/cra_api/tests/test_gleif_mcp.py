import asyncio

import pytest

from cra.external.gleif_mcp import (
    create_gleif_server,
    lookup_lei_via_mcp,
    normalize_lei,
    requested_lei,
)


LEI = "A" * 18 + "12"  # Synthetic test identifier; never a real customer.


def test_router_requires_explicit_single_lei_and_rejects_bad_identifier():
    assert requested_lei(f"请查 LEI {LEI}") == LEI
    assert requested_lei(f"这个客户 {LEI} 的额度") is None
    assert requested_lei("请查 LEI，但号码还没提供") is None
    assert requested_lei(f"对比 LEI {LEI} 与 {'B' * 18 + '34'}") is None
    with pytest.raises(ValueError):
        normalize_lei("../../secrets")


def test_mcp_tool_returns_bounded_source_facts_without_rating_or_bp():
    calls = []

    async def fake_fetch(value):
        calls.append(value)
        return {
            "data": {
                "id": value,
                "attributes": {
                    "entity": {
                        "legalName": {"name": "Synthetic Entity Ltd"},
                        "legalAddress": {"country": "US"},
                    },
                    "registration": {
                        "status": "LAPSED",
                        "lastUpdateDate": "2026-01-01T00:00:00Z",
                    },
                },
            }
        }

    result = asyncio.run(lookup_lei_via_mcp(LEI, create_gleif_server(fake_fetch)))

    assert calls == [LEI]
    assert result["status"] == "found"
    assert result["legal_name"] == "Synthetic Entity Ltd"
    assert result["registration_status"] == "LAPSED"
    assert result["source_url"].endswith(LEI)
    assert "rating" not in result and "bp" not in result


def test_mcp_rejects_mismatched_source_entity():
    async def fake_fetch(value):
        return {"data": {"id": "B" * 18 + "34", "attributes": {}}}

    with pytest.raises(Exception):
        asyncio.run(lookup_lei_via_mcp(LEI, create_gleif_server(fake_fetch)))
