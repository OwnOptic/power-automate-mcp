"""Layer 4: the tools, against a fake Layer 2. Asserts on the calls they make as well
as on what they return, because a wrong route is a bug no return value shows."""

from __future__ import annotations

import pytest

import extras
import server
from conftest import FLOWS, flow, load


# --- server.py ---------------------------------------------------------------------

def test_list_flows_shapes_and_filters(api):
    api.on("GET", FLOWS, {"value": [flow("a", state="Started"), flow("b", state="Stopped")]})
    out = server.list_flows(state="Stopped")
    assert out == [{"flow_id": "b", "display_name": "DEMO", "state": "Stopped",
                    "modified": "2026-11-03T10:00:00.1234567Z"}]


def test_create_flow_validates_before_calling_the_api(api):
    definition = load("demo-flow.json")
    del definition["parameters"]
    with pytest.raises(RuntimeError, match="rejected locally"):
        server.create_flow("DEMO", definition)
    assert api.calls == []


def test_update_snapshots_the_previous_definition(api):
    old, new = load("demo-flow.json"), load("demo-flow.json")
    new["actions"]["Load_settings"]["inputs"]["batch_size"] = 4
    api.on("GET", f"{FLOWS}/f1", flow(definition=old))
    api.on("PATCH", f"{FLOWS}/f1", lambda body: flow(definition=body["properties"]["definition"]))
    out = server.update_flow_definition("f1", new)
    assert out["previous_definition"]["actions"]["Load_settings"]["inputs"]["batch_size"] == 0
    patch_body = next(b for m, p, b in api.calls if m == "PATCH")
    assert "connectionReferences" not in patch_body["properties"]  # connector-free: none sent


def test_update_carries_live_bindings_on_a_connector_flow(api):
    definition = load("demo-flow-connector.json")
    api.on("GET", f"{FLOWS}/f1", flow(definition=definition))
    api.on("GET", f"{FLOWS}/f1/connections", {"value": [
        {"name": "4f9d05ec", "properties": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_teams"}}]})
    api.on("PATCH", f"{FLOWS}/f1", lambda body: flow(definition=body["properties"]["definition"]))
    server.update_flow_definition("f1", definition)
    patch_body = next(b for m, p, b in api.calls if m == "PATCH")
    assert patch_body["properties"]["connectionReferences"]["shared_teams"]["connectionName"] == "4f9d05ec"


def test_bind_connection_refuses_to_guess(api, monkeypatch):
    monkeypatch.setattr(server, "_environment_connections", lambda c: {
        "a": {"connection_name": "a"}, "b": {"connection_name": "b"}})
    out = server.bind_connection("f1", "shared_teams")
    assert out["status"] == "ambiguous" and len(out["candidates"]) == 2
    assert api.paths("PATCH") == []


def test_bind_connection_not_found_hands_over_the_portal_link(api, monkeypatch):
    monkeypatch.setattr(server, "_environment_connections", lambda c: {})
    out = server.bind_connection("f1", "shared_teams")
    assert out["status"] == "not_found"
    assert out["create_url"].endswith("/environments/env-test/connections")


def test_run_flow_posts_to_the_trigger(api):
    api.on("POST", f"{FLOWS}/f1/triggers/manual/run", {"status": "ok"})
    server.run_flow("f1")
    assert api.paths("POST") == [f"{FLOWS}/f1/triggers/manual/run"]


def test_explain_run_resolves_the_error_and_the_upstream_value(api, blobs):
    blobs({"https://blob/out": {"region": "westeurope", "retries": 3, "batch_size": 0}})
    api.on("GET", f"{FLOWS}/f1/runs/r1", {"name": "r1", "properties": {"status": "Failed", "startTime": "t"}})
    api.on("GET", f"{FLOWS}/f1/runs/r1/actions", {"value": [
        {"name": "Load_settings", "properties": {"status": "Succeeded", "outputsLink": {"uri": "https://blob/out"}}},
        {"name": "Compute_batches", "properties": {"status": "Failed", "error": {
            "code": "InvalidTemplate", "message": "The template language function 'div' was invoked with a divisor of zero."}}},
    ]})
    out = server.explain_run("f1", "r1")
    assert out["failed_actions"][0]["name"] == "Compute_batches"
    assert "divisor of zero" in out["failed_actions"][0]["error"]
    assert out["succeeded"][0]["outputs"]["batch_size"] == 0
    assert out["hint"].startswith("Compute_batches failed")


def test_explain_run_points_at_the_trigger_when_no_action_failed(api):
    api.on("GET", f"{FLOWS}/f1/runs/r1", {"name": "r1", "properties": {"status": "Failed"}})
    api.on("GET", f"{FLOWS}/f1/runs/r1/actions", {"value": []})
    assert "trigger" in server.explain_run("f1", "r1")["hint"]


def test_compare_runs_finds_the_upstream_change_same_size_payloads(api, blobs):
    # Both payloads are 50 bytes: comparing contentSize would call them equal.
    blobs({"https://blob/bad": {"region": "westeurope", "retries": 3, "batch_size": 0},
           "https://blob/good": {"region": "westeurope", "retries": 3, "batch_size": 4}})
    api.on("GET", f"{FLOWS}/f1/runs/bad/actions", {"value": [
        {"name": "Load_settings", "properties": {"status": "Succeeded", "outputsLink": {"uri": "https://blob/bad", "contentSize": 50}}},
        {"name": "Compute_batches", "properties": {"status": "Failed"}}]})
    api.on("GET", f"{FLOWS}/f1/runs/good/actions", {"value": [
        {"name": "Load_settings", "properties": {"status": "Succeeded", "outputsLink": {"uri": "https://blob/good", "contentSize": 50}}},
        {"name": "Compute_batches", "properties": {"status": "Succeeded"}}]})
    out = server.compare_runs("f1", "bad", "good")
    assert out["diverged_at"] == "Compute_batches"
    assert out["output_changes"] == [{"action": "Load_settings", "baseline": {"region": "westeurope", "retries": 3, "batch_size": 4},
                                      "failed": {"region": "westeurope", "retries": 3, "batch_size": 0}}]


def test_analyze_flow_health_calls_a_concentrated_failure_flaky(api):
    runs = [{"name": f"s{i}", "properties": {"status": "Succeeded", "startTime": "2026-11-03T10:00:00Z",
                                            "endTime": "2026-11-03T10:00:02Z"}} for i in range(8)]
    runs += [{"name": f"x{i}", "properties": {"status": "Failed"}} for i in range(2)]
    api.on("GET", f"{FLOWS}/f1/runs", {"value": runs})
    for i in range(2):
        api.on("GET", f"{FLOWS}/f1/runs/x{i}/actions", {"value": [
            {"name": "Get_items", "properties": {"status": "Failed", "error": {"message": "not JSON"}}}]})
    out = server.analyze_flow_health("f1")
    assert out["failure_rate"] == 0.2 and out["duration_seconds"]["mean"] == 2.0
    assert out["failing_actions"][0] == {"action": "Get_items", "count": 2, "sample_error": "not JSON"}
    assert "concentrated in 'Get_items'" in out["verdict"]


# --- extras.py ---------------------------------------------------------------------

def test_resubmit_uses_the_trigger_history_route(api):
    api.on("GET", f"{FLOWS}/f1/runs/r1", {"name": "r1", "properties": {"trigger": {"name": "When_a_row_is_added"}}})
    api.on("POST", f"{FLOWS}/f1/triggers/When_a_row_is_added/histories/r1/resubmit", {"status": "ok"})
    out = extras.resubmit_run("f1", "r1")
    assert out["trigger"] == "When_a_row_is_added"
    assert api.paths("POST") == [f"{FLOWS}/f1/triggers/When_a_row_is_added/histories/r1/resubmit"]
    assert not any(p.endswith("/runs/r1/resubmit") for p in api.paths())  # the 404 route


def test_resubmit_without_a_trigger_record_says_so(api):
    api.on("GET", f"{FLOWS}/f1/runs/r1", {"name": "r1", "properties": {}})
    with pytest.raises(RuntimeError, match="run_flow"):
        extras.resubmit_run("f1", "r1")
    assert api.paths("POST") == []


def test_set_flow_state_reads_the_state_back(api):
    api.on("POST", f"{FLOWS}/f1/stop", {"status": "ok"})
    api.on("GET", f"{FLOWS}/f1", flow(state="Stopped"))
    assert extras.set_flow_state("f1", "stopped") == {"flow_id": "f1", "requested": "Stopped", "state": "Stopped", "ok": True}


def test_set_flow_state_reports_a_start_that_did_not_take(api):
    api.on("POST", f"{FLOWS}/f1/start", {"status": "ok"})
    api.on("GET", f"{FLOWS}/f1", flow(state="Stopped"))
    assert extras.set_flow_state("f1", "Started")["ok"] is False


def test_set_flow_state_rejects_other_states(api):
    with pytest.raises(server.PowerAutomateError):
        extras.set_flow_state("f1", "Suspended")
    assert api.calls == []


def test_cancel_run_route(api):
    api.on("POST", f"{FLOWS}/f1/runs/r1/cancel", {"status": "ok"})
    assert extras.cancel_run("f1", "r1")["status"] == "cancelled"


def test_list_environments_marks_the_current_one(api):
    api.on("GET", "/environments", {"value": [
        {"name": "env-test", "location": "europe", "properties": {
            "displayName": "Dev", "environmentSku": "Developer", "isDefault": False,
            "linkedEnvironmentMetadata": {"instanceApiUrl": "https://org.api.crm4.dynamics.com"}}},
        {"name": "Default-t", "location": "europe", "properties": {"displayName": "Default", "isDefault": True}},
    ]})
    dev, default = extras.list_environments()
    assert dev["current"] and dev["has_dataverse"] and dev["sku"] == "Developer"
    assert not default["current"] and not default["has_dataverse"] and default["is_default"]


def test_org_url_explains_an_environment_without_dataverse(api, monkeypatch):
    monkeypatch.setattr(extras, "_org_cache", {})
    api.on("GET", "/environments/env-test", {"properties": {}})
    with pytest.raises(RuntimeError, match="no Dataverse instance"):
        extras._org_url("env-test")


def test_delete_flow_needs_confirm(api):
    assert extras.delete_flow("f1")["status"] == "refused"
    assert api.calls == []
    api.on("DELETE", f"{FLOWS}/f1", {"status": "ok"})
    assert extras.delete_flow("f1", confirm=True)["status"] == "deleted"
