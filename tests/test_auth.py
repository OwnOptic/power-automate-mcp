"""_access_token: the pinned tenant guard, and the fallback when the ACTIVE az account is another tenant's."""

from __future__ import annotations

import pytest

import server


@pytest.fixture
def az(monkeypatch):
    calls: list[list[str]] = []
    outcomes: list[object] = []

    def fake(args):
        calls.append(args)
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(server, "_az", fake)
    fake.calls, fake.outcomes = calls, outcomes
    return fake


def test_pinned_tenant_is_tried_first(az, monkeypatch):
    monkeypatch.setenv("PA_TENANT_ID", "tenant-a")
    az.outcomes.append("tok")
    assert server._access_token("https://org.test") == "tok"
    assert az.calls[0][-2:] == ["--tenant", "tenant-a"]


def test_wrong_active_account_falls_back_to_selecting_the_tenant_login(az, monkeypatch):
    monkeypatch.setenv("PA_TENANT_ID", "tenant-a")
    az.outcomes += [server.PowerAutomateError("AADSTS90072: User account does not exist in tenant"), "tok"]
    assert server._access_token("https://org.test") == "tok"
    assert az.calls[1][-2:] == ["--subscription", "tenant-a"]


def test_other_failures_are_not_retried(az, monkeypatch):
    monkeypatch.setenv("PA_TENANT_ID", "tenant-a")
    az.outcomes.append(server.PowerAutomateError("AADSTS70043: refresh token expired"))
    with pytest.raises(server.PowerAutomateError, match="70043"):
        server._access_token("https://org.test")
    assert len(az.calls) == 1


def test_unpinned_never_retries(az, monkeypatch):
    monkeypatch.delenv("PA_TENANT_ID", raising=False)
    az.outcomes.append(server.PowerAutomateError("AADSTS90072"))
    with pytest.raises(server.PowerAutomateError):
        server._access_token("https://org.test")
    assert len(az.calls) == 1
