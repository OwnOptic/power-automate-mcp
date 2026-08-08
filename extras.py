"""pa-demo-mcp extras - the tools the teaching server deliberately leaves out.

Run this as a SECOND MCP server alongside server.py. The point is not the five
tools; it is the import line below.

    from server import _call, _list, _flow_summary, _run_summary, _trim, env_id

Five more tools, zero new auth code and zero new transport code. Layers 1 and 2
are commodity - written once, reused forever, and an LLM will write them for you
in one prompt. Layer 4 is the part that had to be learned. That is the whole
argument of the talk, expressed as a dependency.

server.py stays exactly ten tools so "ten tools, one loop" remains literally true
on stage. Everything here is off that loop: it is useful, not instructive.

Why these five and not the other fifteen in the production server:

    delete_flow                     nothing else can clean up after create_flow
    resubmit_run                    closes the diagnose loop on the ORIGINAL payload
    get_trigger_url                 the callback URL for HTTP-request triggers
                                    (blocked on Button triggers - see its docstring)
    get_solution_flow_clientdata    the two tools that reach flows the PA REST API
    update_solution_flow_definition cannot touch at all

Register it the same way as server.py, with its own entry:

    "pa-demo-extras": {"command": "python", "args": ["<abs path>/extras.py"]}
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Layers 1-3, unchanged and unduplicated.
from server import _az, _call, _flow_summary, _tenant_args, _trim, env_id

mcp = FastMCP("pa-demo-extras")


# ---------------------------------------------------------------------------
# Dataverse access - the second audience
# ---------------------------------------------------------------------------
# Solution and portal-bound flows do not live behind the Flow API. Their
# definition sits in the Dataverse `workflow` row's `clientdata` column, which
# needs a token whose audience is the environment's own org URL. `az` mints that
# on demand, so it costs a second TOKEN and not a second app registration - the
# same correction that went into server._discover_connections.

_org_cache: dict[str, str] = {}


def _org_url(environment: str) -> str:
    """Resolve an environment's Dataverse org URL, cached per environment.

    Returns e.g. https://org24646865.api.crm12.dynamics.com. Raises if the
    environment has no Dataverse instance - several do not, and the failure is
    worth stating plainly rather than surfacing as a confusing 404 later.
    """
    if environment in _org_cache:
        return _org_cache[environment]
    meta = (
        _call("GET", f"/environments/{environment}")
        .get("properties", {})
        .get("linkedEnvironmentMetadata", {})
    ) or {}
    url = (meta.get("instanceApiUrl") or meta.get("instanceUrl") or "").rstrip("/")
    if not url:
        raise RuntimeError(
            f"Environment {environment} has no Dataverse instance, so it has no "
            "solution flows and nothing here can help. Use server.py's tools."
        )
    _org_cache[environment] = url
    return url


def _dataverse_headers(environment: str) -> dict[str, str]:
    org = _org_url(environment)
    token = _az(["account", "get-access-token", "--resource", org,
                 *_tenant_args(), "--query", "accessToken", "-o", "tsv"])
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }


# ---------------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Flow",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,  # deleting twice lands the same state (second is a 404)
    )
)
def delete_flow(flow_id: str, confirm: bool = False) -> dict:
    """Permanently delete a flow, including its entire run history. No undo.

    `confirm` must be True. The guard is deliberate and was earned: a server that
    can create flows and not remove them leaves litter in every demo, and the
    first cleanup after building this had to be done with a raw API call because
    no tool existed. But delete is the one operation where a confidently wrong
    model call is unrecoverable, so it takes two arguments instead of one.

    Deleting a flow destroys its runs. If those runs are evidence - a failure you
    are still diagnosing, a comparison you have not screenshotted - export what
    you need first.
    """
    if not confirm:
        return {
            "status": "refused",
            "flow_id": flow_id,
            "message": "Pass confirm=True. This deletes the flow and its run history permanently.",
        }
    _call("DELETE", f"/environments/{env_id()}/flows/{flow_id}")
    return {"status": "deleted", "flow_id": flow_id}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Resubmit Run",
        readOnlyHint=False,
        destructiveHint=True,  # re-executes real side effects, same as run_flow
        idempotentHint=False,
        openWorldHint=True,
    )
)
def resubmit_run(flow_id: str, run_id: str) -> dict:
    """Re-run a failed run with its ORIGINAL trigger payload.

    This is what closes the diagnose loop honestly. `run_flow` starts a fresh run
    with no inputs, which for a button-triggered demo is equivalent - but for any
    real flow it is a different run with different data, so a green result proves
    nothing about the failure you were chasing. Resubmit replays the exact payload
    that broke it.

    Same blast radius as the original run: whatever the flow does to the outside
    world, it does again.
    """
    _call("POST", f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}/resubmit")
    return {
        "status": "resubmitted",
        "flow_id": flow_id,
        "source_run": run_id,
        "hint": "Asynchronous. Poll list_runs for the new run, then explain_run it.",
    }


@mcp.tool(annotations=ToolAnnotations(title="Get Trigger URL", readOnlyHint=True))
def get_trigger_url(flow_id: str) -> dict:
    """Get the HTTP callback URL for each request-style trigger on a flow.

    The same URL the portal shows on the trigger card, via listCallbackUrl.

    IT DOES NOT WORK ON BUTTON TRIGGERS, measured 2026-08-07 against a real flow:

        400 The list callback url operation is blocked for triggers of type 'Request'.

    That is the whole `kind: "Button"` family - the free, seeded, demo-friendly
    trigger. Callback URLs are for `kind: "Http"`, which is Premium. So this tool
    is useful on real flows and useless on the demo one, and the honest summary is
    that you cannot poke a button-triggered flow from outside; you start it with
    server.py's run_flow instead. Blocked triggers are reported per trigger under
    `error` rather than failing the call, so a flow with a mix still returns what
    it can.

    TWO SHAPES for the ones that do work, and the second is easy to miss:
    anonymous/key flows return the URL at `value`, while tenant-auth flows nest it
    under `response.value`. Read only the top level and tenant-auth flows silently
    look like they have no URL.

    Treat the result as a SECRET. A callback URL carries its own signature and is
    enough to run the flow - do not paste it into a chat, a ticket or a slide.
    """
    props = _call("GET", f"/environments/{env_id()}/flows/{flow_id}").get("properties", {})
    triggers = (props.get("definition") or {}).get("triggers") or {}

    out = []
    for name, trigger in triggers.items():
        if trigger.get("type") not in ("Request", "ApiConnectionWebhook", "Manual", "Button"):
            continue
        try:
            resp = _call(
                "POST",
                f"/environments/{env_id()}/flows/{flow_id}/triggers/{name}/listCallbackUrl",
            )
        except RuntimeError as exc:
            out.append({"trigger": name, "error": str(exc)[:200]})
            continue
        nested = resp.get("response") or {}
        out.append({
            "trigger": name,
            "kind": trigger.get("kind"),
            "url": nested.get("value") or resp.get("value"),
            "method": nested.get("method") or resp.get("method"),
            "schema": _trim((trigger.get("inputs") or {}).get("schema")),
        })

    return {
        "flow_id": flow_id,
        "triggers": out,
        "warning": "These URLs are credentials. Anyone holding one can run the flow.",
    }


@mcp.tool(annotations=ToolAnnotations(title="Get Solution Flow Clientdata", readOnlyHint=True))
def get_solution_flow_clientdata(flow_id: str) -> dict:
    """Read a solution or portal-bound flow's real definition, out of Dataverse.

    server.py's get_flow cannot give you an editable definition for these. The PA
    REST API normalises action hosts to `connectionName`, while the stored form
    uses `connectionReferenceName`, so a definition read there and written back is
    rejected. The authoritative copy is the `clientdata` column on the Dataverse
    `workflow` row, and that is what this returns.

    Base every edit on the definition from HERE, never on get_flow's.
    """
    r = httpx.get(
        f"{_org_url(env_id())}/api/data/v9.2/workflows({flow_id})"
        "?$select=name,category,statecode,clientdata",
        headers=_dataverse_headers(env_id()), timeout=45,
    )
    r.raise_for_status()
    row = r.json()
    return {
        "flow_id": flow_id,
        "name": row.get("name"),
        "category": row.get("category"),  # 5 = modern cloud flow, 6 = desktop flow
        "statecode": row.get("statecode"),  # 0 draft / 1 activated
        "clientdata": json.loads(row.get("clientdata") or "{}"),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Solution Flow Definition",
        readOnlyHint=False,
        destructiveHint=True,  # full replace, and it deactivates the flow
        idempotentHint=True,
    )
)
def update_solution_flow_definition(
    flow_id: str,
    definition: dict,
    connection_references: dict | None = None,
) -> dict:
    """Replace the definition of a solution or portal-bound flow, via Dataverse.

    The tool server.py's update_flow_definition docstring tells you does not exist.
    Read-modify-write on `clientdata`: read the row, swap in `definition`, PATCH it
    back. Everything the caller does not supply is preserved.

    Leave `connection_references` as None unless you genuinely need to change them.
    Preserving whatever is already there is the safe path - the stored format uses
    connectionReferenceName and guessing at it is how you break a working flow.

    A PATCH to clientdata DEACTIVATES the flow. This reactivates it when it was
    active before, and reports which happened under `reactivated`.
    """
    org, headers = _org_url(env_id()), _dataverse_headers(env_id())
    base = f"{org}/api/data/v9.2/workflows({flow_id})"

    r = httpx.get(f"{base}?$select=clientdata,statecode", headers=headers, timeout=45)
    r.raise_for_status()
    row = r.json()
    was_active = row.get("statecode") == 1

    clientdata = json.loads(row.get("clientdata") or "{}")
    clientdata.setdefault("properties", {})["definition"] = definition
    if connection_references is not None:
        clientdata["properties"]["connectionReferences"] = connection_references

    p = httpx.patch(base, headers=headers, timeout=60,
                    json={"clientdata": json.dumps(clientdata)})
    p.raise_for_status()

    reactivated = False
    if was_active:
        a = httpx.patch(base, headers=headers, timeout=45,
                        json={"statecode": 1, "statuscode": 2})
        reactivated = a.status_code < 300

    return {
        "status": "updated",
        "flow_id": flow_id,
        "was_active": was_active,
        "reactivated": reactivated,
        "previous_definition": (json.loads(row.get("clientdata") or "{}")
                                .get("properties", {}).get("definition")),
    }


if __name__ == "__main__":
    mcp.run()
