"""Keep legacy unit fixtures independent of the local demo rollout flag."""

import pytest


@pytest.fixture(autouse=True)
def disable_live_demo_release_for_unit_fixtures(monkeypatch):
    monkeypatch.setenv("CRA_DEMO_KB_RELEASE_ID", "")
