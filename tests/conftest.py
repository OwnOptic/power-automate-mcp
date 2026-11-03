"""Shared fixtures. Every test here runs offline: no az, no tenant, no network.

The fake replaces Layer 2 (`_call`) with a router, so Layers 3 and 4 - the shaping
and the tools, the part this repo claims the value is in - run exactly as written
against canned API responses. What it cannot prove is that the API still answers
the way those responses say. That is what "Verified, and not" in the README is for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import extras  # noqa: E402
import server  # noqa: E402

ENV = "env-test"
FLOWS = f"/environments/{ENV}/flows"


class FakeAPI:
    """Routes (method, path) to canned responses and records every call made."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, Any]] = []

    def on(self, method: str, path: str, response: Any) -> None:
        """`response` is a value, or a callable taking the request body."""
        self.routes[(method, path)] = response

    def __call__(self, method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        if (method, path) not in self.routes:
            raise server.PowerAutomateError(f"Power Automate API 404: no fake route for {method} {path}")
        response = self.routes[(method, path)]
        return response(body) if callable(response) else response

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    # extras imported _call by name, so it holds its own reference: patch both.
    monkeypatch.setattr(server, "_call", fake)
    monkeypatch.setattr(extras, "_call", fake)
    monkeypatch.setattr(server, "_env_id", ENV)
    return fake


@pytest.fixture
def blobs(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    """Serve SAS content links: map a uri to the JSON its blob holds."""
    store: dict[str, Any] = {}

    class _Resp:
        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def json(self) -> Any:
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    def fake_get(uri: str, **_: Any) -> _Resp:
        if uri not in store:
            raise ConnectionError("SAS URL expired")
        return _Resp(store[uri])

    monkeypatch.setattr(server.httpx, "get", fake_get)
    return store.update


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def flow(flow_id: str = "f1", name: str = "DEMO", state: str = "Started", definition: dict | None = None) -> dict:
    return {
        "name": flow_id,
        "properties": {
            "displayName": name,
            "state": state,
            "lastModifiedTime": "2026-11-03T10:00:00.1234567Z",
            "definition": definition or {},
        },
    }
