"""The MCP surface: what a client actually sees. Tool counts, annotations, output
schemas, and a round trip through the SDK's own call_tool, which is where the
2026-08-17 output-schema bug lived - the API call succeeded and the result then
failed validation, so the model saw an error for a flow that existed."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from mcp import Client

import copilot_studio
import extras
import server
from conftest import FLOWS, flow, load


def tools(module) -> dict:
    return {t.name: t for t in asyncio.run(module.mcp.list_tools())}


def call(module, name: str, arguments: dict[str, Any]):
    """Call a tool the way a real client does: over the protocol, in memory.

    Not MCPServer.call_tool - that re-raises a ToolError and leaves turning it into an
    is_error result to the protocol layer, so it cannot show what the model receives.
    """
    async def run():
        async with Client(module.mcp) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(run())


def test_servers_report_their_own_version():
    async def info(module):
        async with Client(module.mcp) as client:
            return client.server_info
    for module in (server, extras, copilot_studio):
        assert asyncio.run(info(module)).version == server.__version__


def test_the_teaching_core_is_exactly_ten_tools():
    assert sorted(tools(server)) == sorted([
        "list_flows", "get_flow", "create_flow", "update_flow_definition", "bind_connection",
        "run_flow", "list_runs", "explain_run", "compare_runs", "analyze_flow_health"])


def test_extras_adds_nine_and_overlaps_nothing():
    names = set(tools(extras))
    assert len(names) == 9 and not names & set(tools(server))


def test_copilot_studio_adds_nine_and_overlaps_nothing():
    names = set(tools(copilot_studio))
    assert names == {"list_agents", "get_agent", "export_agent", "create_agent_flow", "add_flow_tool",
                     "update_agent_instructions", "upsert_agent_skill", "remove_agent_component", "publish_agent"}
    assert not names & (set(tools(server)) | set(tools(extras)))


@pytest.mark.parametrize("module", [server, extras, copilot_studio])
def test_every_tool_is_annotated_and_documented(module):
    for name, tool in tools(module).items():
        assert tool.annotations is not None and tool.annotations.title, name
        assert tool.description and len(tool.description) > 80, name


@pytest.mark.parametrize("module", [server, extras, copilot_studio])
def test_read_only_hints_match_what_the_tool_does(module):
    writers = {"create_flow", "update_flow_definition", "bind_connection", "run_flow",
               "set_flow_state", "cancel_run", "delete_flow", "resubmit_run",
               "update_solution_flow_definition", "create_agent_flow", "add_flow_tool",
               "update_agent_instructions", "publish_agent", "upsert_agent_skill", "remove_agent_component"}
    for name, tool in tools(module).items():
        assert tool.annotations.read_only_hint is (name not in writers), name


def test_flow_tools_publish_an_output_schema():
    for name in ("get_flow", "create_flow", "update_flow_definition"):
        schema = tools(server)[name].output_schema
        assert schema and "flow_id" in str(schema), name


def test_create_flow_round_trips_through_the_sdk(api):
    # The response has no definition, no warnings, no previous_definition: the keys
    # FastMCP 1.x serialised as null and then rejected. It must come back clean.
    api.on("POST", FLOWS, flow("new", name="DEMO - nightly batch"))
    result = call(server, "create_flow", {"display_name": "DEMO - nightly batch", "definition": load("demo-flow.json")})
    assert not result.is_error, result.content
    assert result.structured_content["flow_id"] == "new"


def test_a_tool_error_reaches_the_model_as_text(api):
    # mcp 2.x shows the model a plain exception as "Error executing tool create_flow"
    # and nothing more. PowerAutomateError is a ToolError, so the reason gets through.
    definition = load("demo-flow.json")
    del definition["parameters"]
    result = call(server, "create_flow", {"display_name": "x", "definition": definition})
    assert result.is_error
    assert "missing-authentication-param" in result.content[0].text
    assert api.calls == []


def test_an_api_error_message_reaches_the_model(api):
    # No route for this flow: the fake raises the same PowerAutomateError _call would.
    result = call(server, "get_flow", {"flow_id": "missing"})
    assert result.is_error and "404" in result.content[0].text


def test_a_plain_exception_would_have_been_hidden(api, monkeypatch):
    # The control for the two tests above: proves the SDK really does swallow the text
    # of anything that is not a ToolError, so those tests are testing something.
    def boom(*_, **__):
        raise RuntimeError("the reason the model needed")
    monkeypatch.setattr(server, "_call", boom)
    result = call(server, "get_flow", {"flow_id": "f1"})
    assert result.is_error and "the reason the model needed" not in result.content[0].text


@pytest.mark.parametrize("module", [server, extras, copilot_studio])
def test_no_bare_exception_can_hide_its_message(module):
    # Guards the port: a `raise RuntimeError(` or a stray raise_for_status() added later
    # would silently become "Error executing tool" for the model. The one
    # raise_for_status() allowed is inside _environment_connections' own try/except.
    source = open(module.__file__, encoding="utf-8").read()
    assert not re.search(r"raise (RuntimeError|ValueError|Exception)\(", source)
    assert len(re.findall(r"\.raise_for_status\(\)", source)) == (1 if module is server else 0)
