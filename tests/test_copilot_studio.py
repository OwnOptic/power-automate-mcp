"""copilot_studio.py: the YAML edits, the guards, and the Dataverse calls each tool makes.

Dataverse is faked at `_dataverse`, the one function every call goes through, so the
tools run as written and the test sees exactly which row each one reads or writes.
"""

from __future__ import annotations

import json

import pytest

import copilot_studio as cs
import server
from conftest import load

AGENT = "bot-1"
BASE = "https://org.test/api/data/v9.2"

GPT_INLINE = (
    "kind: GptComponentMetadata\n"
    "instructions: Du bist ein assistent\n"
    "gptCapabilities:\n  webBrowsing: true\n\n"
    "aISettings:\n  model:\n    modelNameHint: GPT55Chat\n"
)
GPT_EMPTY = "kind: GptComponentMetadata\ninstructions:\ngptCapabilities:\n  webBrowsing: true\n"
GPT_BLOCK = "kind: GptComponentMetadata\ninstructions: |-\n  Line one.\n\n  Line three.\ngptCapabilities:\n  webBrowsing: false\n"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def dv(monkeypatch):
    """Route Dataverse calls by (method, url-suffix); record (method, url, json body)."""
    routes: dict[tuple[str, str], object] = {}
    calls: list[tuple[str, str, object]] = []

    def fake(method, url, **kwargs):
        calls.append((method, url.replace(BASE, ""), kwargs.get("json")))
        key = (method, url.replace(BASE, ""))
        if key not in routes:
            raise server.PowerAutomateError(f"Dataverse {method} 404: Entity Does Not Exist ({key[1]})")
        value = routes[key]
        return _Resp(value(kwargs) if callable(value) else value)

    monkeypatch.setattr(cs, "_dataverse", fake)
    monkeypatch.setattr(cs, "_api", lambda: (BASE, {"Authorization": "Bearer x"}))
    monkeypatch.setattr(server, "_env_id", "env-test")
    fake.routes, fake.calls = routes, calls
    return fake


def components(*rows):
    return {"value": list(rows)}


def gpt(data):
    return {"botcomponentid": "gpt-1", "componenttype": 15, "schemaname": "new_bot.gpt.default",
            "name": "bot", "statecode": 0, "data": data}


# --- instructions YAML: read and write one key, leave the rest -----------------------

@pytest.mark.parametrize("data, text", [
    (GPT_INLINE, "Du bist ein assistent"),
    (GPT_EMPTY, ""),
    (GPT_BLOCK, "Line one.\n\nLine three."),
])
def test_reads_the_three_instruction_shapes(data, text):
    assert cs._instructions_span(data)[0] == text


@pytest.mark.parametrize("data", [GPT_INLINE, GPT_EMPTY, GPT_BLOCK])
def test_writing_changes_only_the_instructions(data):
    new = "Answer about orders.\nUse the order tool."
    out = cs._with_instructions(data, new)
    assert cs._instructions_span(out)[0] == new
    rest_before = data.split("gptCapabilities:", 1)[1]
    assert out.split("gptCapabilities:", 1)[1] == rest_before


def test_writing_empty_instructions_round_trips():
    assert cs._instructions_span(cs._with_instructions(GPT_BLOCK, ""))[0] == ""


def test_a_first_line_starting_with_a_space_keeps_its_indent():
    out = cs._with_instructions(GPT_EMPTY, "  indented first\nsecond")
    assert "instructions: |2-" in out and cs._instructions_span(out)[0].startswith("  indented")


# --- update_agent_instructions ------------------------------------------------------

def test_braces_are_refused_before_anything_is_read(dv):
    with pytest.raises(server.PowerAutomateError, match="Power Fx"):
        cs.update_agent_instructions(AGENT, "Return {\"a\": 1}")
    assert dv.calls == []


def test_update_patches_the_gpt_component_and_returns_the_old_text(dv):
    dv.routes[("GET", "/botcomponents")] = components(gpt(GPT_INLINE))
    dv.routes[("PATCH", "/botcomponents(gpt-1)")] = {}
    out = cs.update_agent_instructions(AGENT, "New text.")
    assert out["previous_instructions"] == "Du bist ein assistent"
    method, url, body = dv.calls[-1]
    assert (method, url) == ("PATCH", "/botcomponents(gpt-1)")
    assert cs._instructions_span(body["data"])[0] == "New text."
    assert "modelNameHint: GPT55Chat" in body["data"]


# --- get_agent --------------------------------------------------------------------

def test_get_agent_sorts_components(dv):
    dv.routes[("GET", f"/bots({AGENT})")] = {"name": "Bot", "schemaname": "new_bot", "synchronizationstatus": json.dumps(
        {"lastFinishedPublishOperation": {"status": "Succeeded", "operationEnd": "2026-11-03T10:00:00Z"}})}
    dv.routes[("GET", "/botcomponents")] = components(
        gpt(GPT_INLINE),
        {"componenttype": 9, "schemaname": "new_bot.topic.Greeting", "name": "Greeting", "statecode": 0, "data": ""},
        {"componenttype": 9, "schemaname": "new_bot.topic.Returns", "name": "Returns", "statecode": 0, "data": ""},
        {"componenttype": 9, "schemaname": "new_bot.action.GetOrderStatus", "name": "Get order status", "statecode": 0,
         "data": "kind: TaskDialog\naction:\n  kind: InvokeFlowTaskAction\n  flowId: f-1\n"},
        {"componenttype": 16, "schemaname": "new_bot.topic.Docs", "name": "Docs", "statecode": 0,
         "data": "kind: KnowledgeSourceConfiguration\nsource:\n  kind: SharePointSearchSource\n"},
    )
    out = cs.get_agent(AGENT)
    assert out["instructions"] == "Du bist ein assistent" and out["model"] == "GPT55Chat" and out["web_browsing"]
    assert out["tools"] == [{"name": "Get order status", "kind": "InvokeFlowTaskAction", "flow_id": "f-1", "enabled": True}]
    assert out["knowledge"] == [{"name": "Docs", "kind": "SharePointSearchSource"}]
    assert out["custom_topics"] == ["Returns"] and out["system_topics"] == ["Greeting"]
    assert out["last_publish"]["status"] == "Succeeded"


# --- create_agent_flow and add_flow_tool -------------------------------------------

def test_agent_contract_names_what_is_missing():
    with pytest.raises(server.PowerAutomateError, match='kind "Skills"'):
        cs._agent_contract(load("demo-flow.json"))
    no_reply = load("demo-agent-flow.json")
    del no_reply["actions"]["Respond_to_the_agent"]
    with pytest.raises(server.PowerAutomateError, match="Respond to the agent"):
        cs._agent_contract(no_reply)
    trigger, response = cs._agent_contract(load("demo-agent-flow.json"))
    assert trigger["kind"] == "Skills" and response["type"] == "Response"


def test_create_agent_flow_writes_a_workflow_row_in_the_solution(dv):
    dv.routes[("POST", "/workflows")] = {"workflowid": "wf-1"}
    dv.routes[("PATCH", "/workflows(wf-1)")] = {}
    out = cs.create_agent_flow("DEMO - orders", load("demo-agent-flow.json"), solution="pamcp")
    assert out == {"flow_id": "wf-1", "display_name": "DEMO - orders", "state": "Started", "solution": "pamcp"}
    body = dv.calls[0][2]
    assert body["category"] == 5 and body["type"] == 1 and body["primaryentity"] == "none"
    assert json.loads(body["clientdata"])["properties"]["definition"]["triggers"]["manual"]["kind"] == "Skills"
    assert dv.calls[1] == ("PATCH", "/workflows(wf-1)", {"statecode": 1, "statuscode": 2})


def test_create_agent_flow_refuses_a_flow_an_agent_cannot_call(dv):
    with pytest.raises(server.PowerAutomateError, match="cannot call"):
        cs.create_agent_flow("x", load("demo-flow.json"))
    assert dv.calls == []


def test_add_flow_tool_refuses_a_my_flows_flow(dv):
    with pytest.raises(server.PowerAutomateError, match="SOLUTION flow"):
        cs.add_flow_tool(AGENT, "plain-flow", "x", "x")


def test_add_flow_tool_builds_the_task_dialog_from_the_flow(dv):
    clientdata = json.dumps({"properties": {"definition": load("demo-agent-flow.json")}})
    dv.routes[("GET", "/workflows(wf-1)")] = {"workflowid": "wf-1", "clientdata": clientdata}
    dv.routes[("GET", f"/bots({AGENT})")] = {"schemaname": "new_bot"}
    dv.routes[("GET", "/botcomponents")] = components()
    dv.routes[("POST", "/botcomponents")] = {}
    out = cs.add_flow_tool(AGENT, "wf-1", "Get order status", 'Looks up an "order".')
    assert out["status"] == "added" and out["schema_name"] == "new_bot.action.GetOrderStatus"
    assert out["inputs"] == ["orderId"] and out["outputs"] == ["status", "eta", "carrier"]
    body = dv.calls[-1][2]
    assert body["componenttype"] == 9 and body["parentbotid@odata.bind"] == f"/bots({AGENT})"
    data = body["data"]
    assert "kind: InvokeFlowTaskAction" in data and "flowId: wf-1" in data and "outputMode: All" in data
    assert 'modelDescription: "Looks up an \\"order\\"."' in data
    assert "propertyName: orderId" in data and "description: \"The order number" in data


def test_add_flow_tool_refreshes_instead_of_duplicating(dv):
    clientdata = json.dumps({"properties": {"definition": load("demo-agent-flow.json")}})
    dv.routes[("GET", "/workflows(wf-1)")] = {"workflowid": "wf-1", "clientdata": clientdata}
    dv.routes[("GET", f"/bots({AGENT})")] = {"schemaname": "new_bot"}
    dv.routes[("GET", "/botcomponents")] = components(
        {"botcomponentid": "c-9", "schemaname": "new_bot.action.GetOrderStatus", "componenttype": 9})
    dv.routes[("PATCH", "/botcomponents(c-9)")] = {}
    assert cs.add_flow_tool(AGENT, "wf-1", "Get order status", "d")["status"] == "refreshed"
    assert not [c for c in dv.calls if c[0] == "POST"]


# --- publish_agent ------------------------------------------------------------------

def test_publish_waits_for_a_new_publish_operation(dv, monkeypatch):
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    dv.routes[("POST", f"/bots({AGENT})/Microsoft.Dynamics.CRM.PvaPublish")] = {}
    future = "2099-01-01T00:00:00.0000000Z"
    dv.routes[("GET", f"/bots({AGENT})")] = {"synchronizationstatus": json.dumps({"lastFinishedPublishOperation": {
        "status": "Succeeded", "operationStart": future, "operationEnd": future}})}
    assert cs.publish_agent(AGENT)["status"] == "Succeeded"


def test_publish_reports_no_change_instead_of_hanging(dv, monkeypatch):
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    monkeypatch.setattr(cs.time, "time", lambda: next(clock))
    dv.routes[("POST", f"/bots({AGENT})/Microsoft.Dynamics.CRM.PvaPublish")] = {}
    old = "2020-01-01T00:00:00Z"
    dv.routes[("GET", f"/bots({AGENT})")] = {"synchronizationstatus": json.dumps({"lastFinishedPublishOperation": {
        "status": "Succeeded", "operationStart": old, "operationEnd": old}})}
    assert cs.publish_agent(AGENT, wait_seconds=60)["status"] == "no_change"
