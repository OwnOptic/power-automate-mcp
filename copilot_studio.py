"""power-automate-mcp, Copilot Studio - the FAQ's "same four layers, different API", made real.

A THIRD server. It edits Copilot Studio agents, and it is here because the most useful
thing a flow can be is a tool an agent calls. The import line is the argument again:

    from server import _call, env_id                       # layers 1 and 2: Flow API
    from extras import _dataverse, _dataverse_headers, _org_url   # the Dataverse audience

Zero new auth code. Copilot Studio agents are Dataverse rows - `bots`, plus one
`botcomponents` row per topic, tool, knowledge source and instruction set - so the same
`az` session that runs flows edits agents, with a token for the environment's org URL.

    list_agents                  the agents in the environment, with publish status
    get_agent                    instructions, model, tools, knowledge, topics
    create_agent_flow            a flow an agent can call: a solution flow, made in Dataverse
    add_flow_tool                plug that flow in as a tool the agent decides to call
    update_agent_instructions    rewrite the instructions, guarded, old text returned
    publish_agent                publish, and wait for the real result

Register it like the others:

    "power-automate-copilot": {"command": "python", "args": ["<abs path>/copilot_studio.py"]}
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from extras import _dataverse, _dataverse_headers, _org_url
from server import (
    PowerAutomateError,
    __version__,
    _normalise_definition,
    _parse_time,
    _raise_on_errors,
    _validate_definition,
    env_id,
)

mcp = MCPServer("power-automate-copilot", version=__version__)

# Dataverse stamps a publish's operationStart with its own clock; allow it to run a little
# behind ours, or a publish that really happened reads as "no new publish".
_CLOCK_SKEW = timedelta(seconds=30)

# botcomponent.componenttype values, as stored. 9 covers topics, tools and child agents
# alike; the schema name says which (<bot>.topic.X, <bot>.action.X, <bot>.agent.X).
_TOPIC_OR_TOOL, _INSTRUCTIONS, _KNOWLEDGE = 9, 15, 16

# The topics every new agent ships with. Listed apart so a model reading get_agent sees
# the ones somebody actually wrote.
_SYSTEM_TOPICS = {
    "ConversationStart", "Greeting", "Goodbye", "ThankYou", "StartOver", "ResetConversation",
    "Escalate", "Fallback", "OnError", "EndofConversation", "MultipleTopicsMatched", "Search", "Signin",
}


# ---------------------------------------------------------------------------
# Dataverse plumbing for the two tables
# ---------------------------------------------------------------------------

def _api() -> tuple[str, dict[str, str]]:
    return f"{_org_url(env_id())}/api/data/v9.2", _dataverse_headers(env_id())


def _bot(agent_id: str, select: str) -> dict:
    base, headers = _api()
    return _dataverse("GET", f"{base}/bots({agent_id})", params={"$select": select}, headers=headers).json()


def _components(agent_id: str) -> list[dict]:
    base, headers = _api()
    return _dataverse("GET", f"{base}/botcomponents", headers=headers, params={
        "$select": "botcomponentid,componenttype,schemaname,name,statecode,data",
        "$filter": f"_parentbotid_value eq {agent_id}",
    }).json().get("value", [])


def _agent_contract(definition: dict) -> tuple[dict, dict]:
    """The trigger and reply an agent needs, or a PowerAutomateError saying which is missing.

    trigger  type "Request", kind "Skills"   - "When an agent calls the flow"
    reply    type "Response", kind "Skills"  - "Respond to the agent", anywhere in the tree
    """
    trigger = next(iter((definition.get("triggers") or {}).values()), {})
    if trigger.get("type") != "Request" or trigger.get("kind") != "Skills":
        raise PowerAutomateError(
            f'An agent cannot call this flow: its trigger is type "{trigger.get("type")}", kind '
            f'"{trigger.get("kind")}". It needs type "Request", kind "Skills" (When an agent calls the flow).'
        )

    def find(actions: dict) -> dict | None:
        for action in (actions or {}).values():
            if action.get("type") == "Response" and action.get("kind") == "Skills":
                return action
            for nested in (action.get("actions"), (action.get("else") or {}).get("actions")):
                hit = find(nested or {})
                if hit:
                    return hit
        return None

    response = find(definition.get("actions") or {})
    if not response:
        raise PowerAutomateError(
            'An agent cannot read this flow\'s result: it has no action of type "Response", kind '
            '"Skills" (Respond to the agent). Add one, with its outputs declared in inputs.schema.'
        )
    return trigger, response


def _workflow(flow_id: str) -> dict | None:
    """The flow's Dataverse `workflow` row, or None when it has none (a My flows flow)."""
    base, headers = _api()
    try:
        return _dataverse("GET", f"{base}/workflows({flow_id})", headers=headers,
                          params={"$select": "workflowid,name,statecode,clientdata"}).json()
    except PowerAutomateError as exc:
        if "404" in str(exc) or "Does Not Exist" in str(exc):
            return None
        raise


def _last_publish(sync: str | None) -> dict | None:
    """The bot's last finished publish, from synchronizationstatus (a JSON string column)."""
    try:
        op = (json.loads(sync or "{}").get("lastFinishedPublishOperation") or {})
    except ValueError:
        return None
    return {"status": op.get("status"), "finished": op.get("operationEnd")} if op else None


# ---------------------------------------------------------------------------
# The instructions live in YAML. Edit one key, leave every other byte alone.
# ---------------------------------------------------------------------------

def _instructions_span(data: str) -> tuple[str, int, int]:
    """Find the top-level `instructions:` key in a gpt.default component.

    Returns (text, first_line, end_line). Handles the three shapes measured in real
    agents: an inline scalar ("instructions: Du bist ..."), an empty key followed by
    the next key, and a block scalar ("instructions: |-" then indented lines).
    No YAML library on purpose: re-serialising the whole document with one would
    reorder keys and requote strings in parts of the agent nobody asked to change.
    """
    lines = data.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("instructions:"):
            continue
        head = line[len("instructions:"):].strip()
        j = i + 1
        while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1  # trailing blank lines belong to the gap before the next key
        body = lines[i + 1:j]
        if head[:1] in ("|", ">"):
            indent = min((len(b) - len(b.lstrip()) for b in body if b.strip()), default=0)
            text = "\n".join(b[indent:] for b in body)
            if head.startswith(">"):
                text = " ".join(t.strip() for t in text.splitlines() if t.strip())
        elif head.startswith('"'):
            text = json.loads(" ".join([head] + [b.strip() for b in body]))
        elif head.startswith("'"):
            text = " ".join([head] + [b.strip() for b in body])[1:-1].replace("''", "'")
        else:
            text = " ".join([head] + [b.strip() for b in body]).strip()
        return text, i, j
    raise PowerAutomateError("This agent's instruction component has no `instructions:` key.")


def _with_instructions(data: str, text: str) -> str:
    _, start, end = _instructions_span(data)
    lines = data.splitlines()
    if not text.strip():
        new = ["instructions:"]
    else:
        body = text.rstrip().splitlines()
        # A block whose first line starts with a space needs an explicit indent indicator.
        new = ["instructions: |2-" if body[0][:1] == " " else "instructions: |-"]
        new += [f"  {b}" if b.strip() else "" for b in body]
    return "\n".join(lines[:start] + new + lines[end:]) + ("\n" if data.endswith("\n") else "")


# ---------------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(title="List Agents", readOnlyHint=True))
def list_agents() -> list[dict]:
    """List the Copilot Studio agents in the current environment.

    Agents live in Dataverse, so this needs an environment with a Dataverse instance
    (list_environments in extras.py says which). The environment is the one every
    other tool uses: PA_ENV_ID, or the tenant default.

    Returns [{agent_id, name, schema_name, last_publish, published_on}]. `agent_id`
    feeds every other tool here. `last_publish` is {status, finished} from the agent's
    own sync record, or None for an agent that was never published - in which case
    nothing you change is live for anyone until publish_agent runs.
    """
    base, headers = _api()
    rows = _dataverse("GET", f"{base}/bots", headers=headers, params={
        "$select": "botid,name,schemaname,publishedon,synchronizationstatus",
        "$orderby": "name asc",
    }).json().get("value", [])
    return [{
        "agent_id": b.get("botid"),
        "name": b.get("name"),
        "schema_name": b.get("schemaname"),
        "last_publish": _last_publish(b.get("synchronizationstatus")),
        "published_on": b.get("publishedon"),
    } for b in rows]


@mcp.tool(annotations=ToolAnnotations(title="Get Agent", readOnlyHint=True))
def get_agent(agent_id: str) -> dict:
    """Read an agent the way its maker thinks about it: instructions, tools, knowledge, topics.

    Call this before update_agent_instructions or add_flow_tool. The instructions come
    back as plain text, exactly what the Instructions box in Copilot Studio shows.

    Returns {agent_id, name, schema_name, instructions, model, web_browsing, tools,
    knowledge, child_agents, custom_topics, system_topics, last_publish}.
    `tools` lists each tool's display name and kind - InvokeFlowTaskAction is a Power
    Automate flow, with its flow_id. System topics are the ones every agent ships with;
    `custom_topics` are the ones somebody wrote.
    """
    bot = _bot(agent_id, "botid,name,schemaname,synchronizationstatus")
    out: dict[str, Any] = {
        "agent_id": agent_id, "name": bot.get("name"), "schema_name": bot.get("schemaname"),
        "instructions": None, "model": None, "web_browsing": None, "tools": [], "knowledge": [],
        "child_agents": [], "custom_topics": [], "system_topics": [],
        "last_publish": _last_publish(bot.get("synchronizationstatus")),
    }
    for c in _components(agent_id):
        schema, data = c.get("schemaname") or "", c.get("data") or ""
        if c.get("componenttype") == _INSTRUCTIONS and schema.endswith(".gpt.default"):
            out["instructions"] = _instructions_span(data)[0]
            model = re.search(r"modelNameHint:\s*(\S+)", data)
            out["model"] = model.group(1) if model else None
            out["web_browsing"] = bool(re.search(r"webBrowsing:\s*true", data))
        elif c.get("componenttype") == _KNOWLEDGE:
            source = re.search(r"source:\s*\n\s*kind:\s*(\S+)", data)
            out["knowledge"].append({"name": c.get("name"), "kind": source.group(1) if source else None})
        elif ".action." in schema:
            kind = re.search(r"action:\s*\n\s*kind:\s*(\S+)", data)
            flow = re.search(r"flowId:\s*(\S+)", data)
            out["tools"].append({"name": c.get("name"), "kind": kind.group(1) if kind else None,
                                 "flow_id": flow.group(1) if flow else None,
                                 "enabled": c.get("statecode") == 0})
        elif ".agent." in schema:
            out["child_agents"].append(c.get("name"))
        elif ".topic." in schema:
            short = schema.split(".topic.", 1)[1]
            (out["system_topics"] if short in _SYSTEM_TOPICS else out["custom_topics"]).append(c.get("name") or short)
    return out


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Agent Instructions",
        readOnlyHint=False,
        destructiveHint=True,  # replaces the whole text; the old one comes back in the result
        idempotentHint=True,
    )
)
def update_agent_instructions(agent_id: str, instructions: str) -> dict:
    """Replace an agent's instructions - the Instructions box in Copilot Studio.

    Send the COMPLETE new text: this replaces, it does not append. Read the current
    text with get_agent first, edit it, send it back. The result carries
    `previous_instructions`, which is the only undo there is.

    NO CURLY BRACES. Copilot Studio parses `{` and `}` in instructions as Power Fx, so a
    literal JSON example in the text breaks the agent at runtime with a
    ContentValidationError on EVERY message, while the save itself succeeds. This tool
    refuses them before saving. Describe a JSON shape by field names instead; square
    brackets are fine.

    Only the instructions key changes. Model, web browsing and every other setting in
    the component are left byte for byte as they were.

    NOT LIVE UNTIL PUBLISHED. The test pane in Copilot Studio picks the change up; Teams,
    Microsoft 365 Copilot and every other channel keep the published version until you
    run publish_agent.
    """
    if "{" in instructions or "}" in instructions:
        raise PowerAutomateError(
            "Refused: the instructions contain { or }. Copilot Studio parses braces as Power Fx "
            "and the agent then fails on every message with ContentValidationError. Describe the "
            "shape by field names, or put a literal example in a knowledge file."
        )
    gpt = next((c for c in _components(agent_id)
                if c.get("componenttype") == _INSTRUCTIONS and (c.get("schemaname") or "").endswith(".gpt.default")), None)
    if not gpt:
        raise PowerAutomateError("This agent has no instruction component (gpt.default). Is it a classic, topic-only bot?")
    previous = _instructions_span(gpt["data"])[0]
    base, headers = _api()
    _dataverse("PATCH", f"{base}/botcomponents({gpt['botcomponentid']})", headers=headers,
               json={"data": _with_instructions(gpt["data"], instructions)})
    out: dict[str, Any] = {
        "status": "updated", "agent_id": agent_id, "characters": len(instructions),
        "previous_instructions": previous,
        "hint": "Saved as a draft: the test pane sees it now, channels see it after publish_agent.",
    }
    if len(instructions) > 8000:
        out["warning"] = "Over 8000 characters: the classic limit. Newer agents can lift it; older ones truncate."
    return out


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Agent Flow",
        readOnlyHint=False,
        destructiveHint=False,  # additive: a new flow
        idempotentHint=False,  # twice gives two flows
    )
)
def create_agent_flow(display_name: str, definition: dict, solution: str = "", start: bool = True) -> dict:
    """Create a flow an agent can call, as a SOLUTION flow. Use this, not create_flow, for agent tools.

    WHY A SEPARATE TOOL, measured 2026-11-03: Copilot Studio only calls solution flows,
    and a flow made through the Power Automate API (create_flow) is a My flows flow with
    NO row in the Dataverse `workflow` table at all - so an agent tool pointing at it has
    nothing to point at. This tool writes the `workflow` row directly, which is what makes
    a flow solution-aware, then turns it on.

    The definition must have:
      trigger  {"type": "Request", "kind": "Skills"} with inputs in inputs.schema.properties.
               Each input's `description` is what the agent reads to fill it - write it.
      reply    an action {"type": "Response", "kind": "Skills"} ("Respond to the agent"),
               outputs in inputs.schema.properties, the values in inputs.body.
    plus the usual $connections / $authentication parameters. It is validated locally
    first, with the same rules as create_flow.

    `solution` is a solution's unique name (list_solutions in extras.py). Empty puts the
    flow in the environment's default solution, which works; a named one keeps it with
    the agent for export and import. Connector-free flows only: a flow with connector
    actions needs Dataverse connection references, which this does not create.

    Returns {flow_id, display_name, state, solution}. flow_id works with every other
    tool - run_flow, list_runs, explain_run - and with add_flow_tool.
    """
    notes = _normalise_definition(definition)
    _raise_on_errors(_validate_definition(definition))
    _agent_contract(definition)
    base, headers = _api()
    if solution:
        headers = {**headers, "MSCRM.SolutionUniqueName": solution}
    created = _dataverse("POST", f"{base}/workflows", headers={**headers, "Prefer": "return=representation"}, json={
        "name": display_name, "category": 5, "type": 1, "primaryentity": "none",
        "clientdata": json.dumps({"properties": {"connectionReferences": {}, "definition": definition},
                                  "schemaVersion": "1.0.0.0"}),
    }).json()
    flow_id = created["workflowid"]
    state = "Stopped"
    if start:
        _dataverse("PATCH", f"{base}/workflows({flow_id})", headers=headers, json={"statecode": 1, "statuscode": 2})
        state = "Started"
    out = {"flow_id": flow_id, "display_name": display_name, "state": state, "solution": solution or "default"}
    if notes:
        out["warnings"] = notes
    return out


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Flow Tool",
        readOnlyHint=False,
        destructiveHint=False,  # adds a tool, or refreshes one this tool added before
        idempotentHint=True,
    )
)
def add_flow_tool(agent_id: str, flow_id: str, display_name: str, description: str) -> dict:
    """Give an agent a Power Automate flow as a tool it can decide to call.

    The flow must be one an agent CAN call: a SOLUTION flow with the agent trigger and
    reply - which is exactly what create_agent_flow makes. A flow from create_flow is
    refused (it lives in My flows, and Copilot Studio only calls solution flows), and so
    is a flow without the trigger or reply, each with the reason. Refusing here matters:
    a tool bound to a flow the agent cannot call saves fine, even publishes, and then
    fails in conversation.

    `description` is the most important argument. It is what the agent's orchestrator
    reads to decide WHEN to call the tool: write what it does, what it needs and what it
    returns, the way you would brief a colleague. Each input's description comes from
    the flow's trigger schema, so describe the inputs there.

    Inputs and outputs are read from the flow itself; you do not pass them. Calling it
    again for the same flow refreshes the tool (after you changed the flow's inputs, for
    example) instead of adding a second one.

    Not live until publish_agent.
    """
    row = _workflow(flow_id)
    if row is None:
        raise PowerAutomateError(
            "An agent can only call a SOLUTION flow, and this one has no Dataverse workflow row: it was "
            "made in My flows (create_flow does that). Recreate it with create_agent_flow."
        )
    definition = (json.loads(row.get("clientdata") or "{}").get("properties") or {}).get("definition") or {}
    trigger, response = _agent_contract(definition)
    in_props = ((trigger.get("inputs") or {}).get("schema") or {}).get("properties") or {}
    out_props = (((response.get("inputs") or {}).get("schema")) or {}).get("properties") or {}

    def q(text: str) -> str:
        return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid YAML double-quoted scalar

    lines = ["kind: TaskDialog"]
    if in_props:
        lines.append("inputs:")
        for name, spec in in_props.items():
            lines += ["  - kind: AutomaticTaskInput", f"    propertyName: {name}",
                      f"    description: {q(spec.get('description') or spec.get('title') or name)}", ""]
    lines += [f"modelDisplayName: {q(display_name)}", f"modelDescription: {q(description)}"]
    if out_props:
        lines.append("outputs:")
        for name in out_props:
            lines += [f"  - propertyName: {name}", ""]
    lines += ["action:", "  kind: InvokeFlowTaskAction", f"  flowId: {flow_id}", "", "outputMode: All", ""]
    data = "\n".join(lines)

    bot = _bot(agent_id, "schemaname")
    key = re.sub(r"[^A-Za-z0-9]", "", display_name.title()) or "FlowTool"
    schema = f"{bot['schemaname']}.action.{key}"
    base, headers = _api()
    existing = next((c for c in _components(agent_id) if c.get("schemaname") == schema), None)
    if existing:
        _dataverse("PATCH", f"{base}/botcomponents({existing['botcomponentid']})", headers=headers,
                   json={"data": data, "name": display_name})
        status = "refreshed"
    else:
        _dataverse("POST", f"{base}/botcomponents", headers=headers, json={
            "name": display_name, "schemaname": schema, "componenttype": _TOPIC_OR_TOOL, "data": data,
            "parentbotid@odata.bind": f"/bots({agent_id})",
        })
        status = "added"
    return {
        "status": status, "agent_id": agent_id, "tool": display_name, "schema_name": schema, "flow_id": flow_id,
        "inputs": list(in_props), "outputs": list(out_props),
        "hint": "Run publish_agent to make it live. The flow must stay On for the agent to call it.",
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Publish Agent",
        readOnlyHint=False,
        destructiveHint=False,  # makes the current draft live; republishing is how you roll forward
        idempotentHint=True,
        openWorldHint=True,  # every channel the agent is on sees the change
    )
)
def publish_agent(agent_id: str, wait_seconds: int = 120) -> dict:
    """Publish an agent: make its current draft live on every channel it is on.

    Instruction and tool changes are drafts until this runs. Publishing takes 10 to 60
    seconds; this waits for the real result (up to `wait_seconds`) instead of trusting
    the request's 204, and returns {status, started, finished}.

    status "Succeeded" is the only one that means live. "Failed" means the agent has an
    error Copilot Studio will show in its Topics or Tools pages - a tool bound to a flow
    that is off, or a topic referring to something deleted. "timeout" means it is still
    running: call get_agent in a minute and read last_publish.

    Publishing an agent with no change since the last publish records no new publish
    operation; this reports that as "no_change" rather than waiting forever.
    """
    started = datetime.now(timezone.utc)
    base, headers = _api()
    _dataverse("POST", f"{base}/bots({agent_id})/Microsoft.Dynamics.CRM.PvaPublish", headers=headers, json={})
    deadline = time.time() + max(10, wait_seconds)
    while time.time() < deadline:
        time.sleep(5)
        sync = _bot(agent_id, "synchronizationstatus").get("synchronizationstatus")
        try:
            op = json.loads(sync or "{}").get("lastFinishedPublishOperation") or {}
        except ValueError:
            op = {}
        op_start = _parse_time(op.get("operationStart"))
        if op_start and op_start >= started.replace(microsecond=0) - _CLOCK_SKEW:
            return {"status": op.get("status"), "agent_id": agent_id,
                    "started": op.get("operationStart"), "finished": op.get("operationEnd")}
    last = _last_publish(sync)
    return {"status": "no_change" if last and last["status"] == "Succeeded" else "timeout",
            "agent_id": agent_id, "last_publish": last,
            "hint": "No new publish recorded. If nothing changed since the last publish, nothing needed publishing."}


if __name__ == "__main__":
    mcp.run()
