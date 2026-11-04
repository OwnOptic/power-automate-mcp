"""power-platform-mcp extras - the tools the teaching server deliberately leaves out.

Run this as a SECOND MCP server alongside server.py. The point is not the nine
tools; it is the import line below.

    from server import _az, _call, _flow_summary, _list, _tenant_args, _trim, env_id

Nine more tools, zero new auth code and zero new transport code. Layers 1 and 2
are commodity - written once, reused forever, and an LLM will write them for you
in one prompt. Layer 4 is the part that had to be learned. That is the whole
argument of the talk, expressed as a dependency.

server.py stays exactly ten tools so "ten tools, one loop" remains literally true
on stage. Everything here is off that loop: it is useful, not instructive.

Why these nine and not the rest of the production server:

    list_environments               find the PA_ENV_ID you meant, before you need it
    set_flow_state                  start or stop a flow, verified by reading it back
    cancel_run                      stop a run that is looping or hung
    resubmit_run                    closes the diagnose loop on the ORIGINAL payload
    delete_flow                     nothing else can clean up after create_flow
    get_trigger_url                 the callback URL for HTTP-request triggers
                                    (blocked on Button triggers - see its docstring)
    list_solutions                  the three tools that reach the Dataverse side,
    get_solution_flow_clientdata    where solution flows live and the PA REST API
    update_solution_flow_definition cannot follow

Register it the same way as server.py, with its own entry:

    "power-platform-extras": {"command": "python", "args": ["<abs path>/extras.py"]}
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

# Layers 1-3, unchanged and unduplicated.
from server import (
    PowerAutomateError,
    __version__,
    _access_token,
    _call,
    _flow_summary,
    _list,
    _trim,
    env_id,
)

mcp = MCPServer("power-platform-extras", version=__version__)


# ---------------------------------------------------------------------------
# Dataverse access - the second audience
# ---------------------------------------------------------------------------
# Solution flows do not live behind the Flow API. Their definition sits in the
# Dataverse `workflow` row's `clientdata` column, which
# needs a token whose audience is the environment's own org URL. `az` mints that
# on demand, so it costs a second TOKEN and not a second app registration - the
# same correction that went into server._discover_connections.

_org_cache: dict[str, str] = {}


def _org_url(environment: str) -> str:
    """Resolve an environment's Dataverse org URL, cached per environment.

    Returns e.g. https://orgXXXXXXXX.api.crm4.dynamics.com. Raises if the
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
        raise PowerAutomateError(
            f"Environment {environment} has no Dataverse instance, so it has no "
            "solution flows and nothing here can help. Use server.py's tools."
        )
    _org_cache[environment] = url
    return url


def _dataverse_headers(environment: str) -> dict[str, str]:
    org = _org_url(environment)
    token = _access_token(org)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }


def _dataverse(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One Dataverse Web API call, with Dataverse's own error text on failure.

    Not raise_for_status(): an httpx.HTTPStatusError is "a crash" to mcp 2.x, so the
    model would see a bare "Error executing tool" and never the reason - a missing
    privilege, a bad column name, a managed component - that Dataverse put in the body.
    """
    r = httpx.request(method, url, timeout=kwargs.pop("timeout", 45), **kwargs)
    if r.status_code >= 300:
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:400]
        except ValueError:
            msg = r.text[:400]
        raise PowerAutomateError(f"Dataverse {method} {r.status_code}: {msg}")
    return r


# ---------------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(title="List Environments", readOnlyHint=True))
def list_environments() -> list[dict]:
    """List the Power Platform environments the signed-in account can reach.

    Use this BEFORE setting PA_ENV_ID, not after a 404. The ids here are exactly what
    PA_ENV_ID takes, and `current` marks the one every other tool is pointed at right
    now - which, with PA_ENV_ID unset, is the tenant's Default environment.

    `has_dataverse` tells you in advance whether the solution tools below can work
    there: list_solutions, get_solution_flow_clientdata and
    update_solution_flow_definition all need a Dataverse instance, and several
    environments (the Default one often included) have none.

    Returns [{environment_id, display_name, sku, region, is_default, has_dataverse,
    current}]. The environment's own state is left out: every one this lists is one
    you can open, and the rest is portal plumbing.
    """
    current = env_id()
    out = []
    for e in _list("/environments", cap=200):
        props = e.get("properties", {}) or {}
        meta = props.get("linkedEnvironmentMetadata") or {}
        out.append({
            "environment_id": e.get("name"),
            "display_name": props.get("displayName"),
            "sku": props.get("environmentSku"),
            "region": e.get("location"),
            "is_default": bool(props.get("isDefault")),
            "has_dataverse": bool(meta.get("instanceApiUrl") or meta.get("instanceUrl")),
            "current": e.get("name") == current,
        })
    return out


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Flow State",
        readOnlyHint=False,
        destructiveHint=False,  # stopping pauses triggers; nothing is lost and start undoes it
        idempotentHint=True,  # asking for the state a flow is already in changes nothing
    )
)
def set_flow_state(flow_id: str, state: str) -> dict:
    """Start or stop a flow. `state` is "Started" or "Stopped".

    VERIFY WITH THE RETURNED STATE, NOT WITH THE 200. The start and stop endpoints
    return a receipt, not a state, and the portal's details page has been seen reading
    "Off" after a click that did nothing. So this reads the flow back and returns `ok`,
    which says whether the state actually moved.

    Starting a connector flow whose connections are not bound fails with
    CannotStartUnpublishedSolutionFlow. That is not a state problem: bind it first with
    bind_connection in server.py, which also starts it.

    Stopping is the safe brake for a flow that misbehaves on a schedule or a trigger.
    It does NOT stop a run already in progress - use cancel_run for that.
    """
    want = state.strip().capitalize()
    if want not in ("Started", "Stopped"):
        raise PowerAutomateError(f"state must be 'Started' or 'Stopped', got {state!r}")
    verb = "start" if want == "Started" else "stop"
    _call("POST", f"/environments/{env_id()}/flows/{flow_id}/{verb}")
    # Read it back: the POST's 200 is a receipt, not a state.
    now = _flow_summary(_call("GET", f"/environments/{env_id()}/flows/{flow_id}"))
    return {"flow_id": flow_id, "requested": want, "state": now["state"], "ok": now["state"] == want}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Cancel Run",
        readOnlyHint=False,
        destructiveHint=True,  # the run ends where it is; finished actions are NOT rolled back
        idempotentHint=True,
    )
)
def cancel_run(flow_id: str, run_id: str) -> dict:
    """Cancel a run that is still Running: a loop that will not end, a wait that never returns.

    Only a Running run can be cancelled. A finished one returns an error from the API,
    which is passed through as is.

    NOTHING IS UNDONE. Actions that already ran stay done - the mail is sent, the row
    is written. Cancelling stops what has not happened yet. If the flow will just
    start again on its next trigger, stop it first with set_flow_state.

    The run ends as Cancelled. list_runs shows it, and explain_run on it lists what had
    succeeded before the cancel, which is usually what you want to check next.
    """
    _call("POST", f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}/cancel")
    return {
        "status": "cancelled",
        "flow_id": flow_id,
        "run_id": run_id,
        "hint": "Finished actions are not rolled back. Run explain_run to see what had completed.",
    }


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

    THE OBVIOUS ROUTE DOES NOT EXIST. `POST /flows/{flow}/runs/{run}/resubmit` reads
    like the right call, sits next to the real `/runs/{run}/cancel`, and returns 404.
    This tool shipped calling it, and so did the README's own example of how to add
    it. Resubmit belongs to the TRIGGER'S history, not to the run:

        POST /flows/{flow}/triggers/{trigger}/histories/{run}/resubmit

    The history name is the run id (the Logic Apps reference says it "corresponds to
    the run name for triggers that resulted in a run"), and the trigger name is read
    off the run itself, so you only pass the run_id.

    ONLY RUNS THE SERVICE STARTED, measured 2026-11-03. A run fired by its own trigger
    (a schedule, a new email, a new row) resubmits fine. A run started through run_flow
    is refused:

        403 This flow run was started by someone else and you can only resubmit
            runs that you've initiated.

    To retest a fix on a Button flow, use run_flow again; there is no payload to
    replay anyway.
    """
    run = _call("GET", f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}")
    trigger = ((run.get("properties", {}) or {}).get("trigger") or {}).get("name")
    if not trigger:
        raise PowerAutomateError(
            f"Run {run_id} has no trigger record, so it cannot be resubmitted. "
            "Start a fresh run with run_flow instead."
        )
    _call("POST", f"/environments/{env_id()}/flows/{flow_id}/triggers/{trigger}/histories/{run_id}/resubmit")
    return {
        "status": "resubmitted",
        "flow_id": flow_id,
        "source_run": run_id,
        "trigger": trigger,
        "hint": "Asynchronous. Poll list_runs for the new run, then explain_run it.",
    }


@mcp.tool(annotations=ToolAnnotations(title="Get Trigger URL", readOnlyHint=True))
def get_trigger_url(flow_id: str) -> dict:
    """Get the HTTP callback URL for each request-style trigger on a flow.

    The same URL the portal shows on the trigger card, via listCallbackUrl.

    IT DOES NOT WORK ON BUTTON TRIGGERS, measured 2026-08-17 against a real flow:

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


@mcp.tool(annotations=ToolAnnotations(title="List Solutions", readOnlyHint=True))
def list_solutions(top: int = 50, include_hidden: bool = False) -> list[dict]:
    """List the solutions in the current environment, read from Dataverse.

    Solutions are Dataverse rows, not Power Automate resources. There is no
    /providers/Microsoft.ProcessSimple/environments/{env}/solutions route: it returns
    404 "No HTTP resource was found" for every environment, valid or not. So this
    resolves the environment's org URL and queries the `solutions` table, with a
    second token from `az` - a second audience, not a second app registration.

    Use it to answer "which solution is this flow in, and is it managed?" before you
    edit anything. A MANAGED solution's flows are read-only by design: edit them in the
    unmanaged source environment and re-import, not here.

    Hidden system solutions (isvisible = false) are filtered out by default, matching
    what the maker portal shows. Pass include_hidden=True for a governance audit.

    Returns [{solution_id, unique_name, display_name, version, publisher, is_managed,
    installed_on}]. Raises with a plain explanation on an environment that has no
    Dataverse instance - list_environments shows which ones do.
    """
    params = {
        "$select": "solutionid,uniquename,friendlyname,version,ismanaged,installedon",
        "$expand": "publisherid($select=friendlyname)",
        "$orderby": "friendlyname asc",
        "$top": str(max(1, min(int(top), 500))),
    }
    if not include_hidden:
        params["$filter"] = "isvisible eq true"
    r = _dataverse("GET", f"{_org_url(env_id())}/api/data/v9.2/solutions", params=params,
                   headers=_dataverse_headers(env_id()))
    return [
        {
            "solution_id": s.get("solutionid"),
            "unique_name": s.get("uniquename"),
            "display_name": s.get("friendlyname"),
            "version": s.get("version"),
            "publisher": (s.get("publisherid") or {}).get("friendlyname"),
            "is_managed": bool(s.get("ismanaged")),
            "installed_on": s.get("installedon"),
        }
        for s in r.json().get("value", [])
    ]


@mcp.tool(annotations=ToolAnnotations(title="Get Solution Flow Clientdata", readOnlyHint=True))
def get_solution_flow_clientdata(flow_id: str) -> dict:
    """Read a solution flow's real definition, out of Dataverse.

    server.py's get_flow cannot give you an editable definition for these. The PA
    REST API normalises action hosts to `connectionName`, while the stored form
    uses `connectionReferenceName`, so a definition read there and written back is
    rejected. The authoritative copy is the `clientdata` column on the Dataverse
    `workflow` row, and that is what this returns.

    Base every edit on the definition from HERE, never on get_flow's.
    """
    row = _dataverse(
        "GET",
        f"{_org_url(env_id())}/api/data/v9.2/workflows({flow_id})"
        "?$select=name,category,statecode,clientdata",
        headers=_dataverse_headers(env_id()),
    ).json()
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
    """Replace the definition of a solution flow, via Dataverse.

    The tool server.py's update_flow_definition docstring sends you to for these.
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

    row = _dataverse("GET", f"{base}?$select=clientdata,statecode", headers=headers).json()
    was_active = row.get("statecode") == 1

    clientdata = json.loads(row.get("clientdata") or "{}")
    clientdata.setdefault("properties", {})["definition"] = definition
    if connection_references is not None:
        clientdata["properties"]["connectionReferences"] = connection_references

    _dataverse("PATCH", base, headers=headers, timeout=60, json={"clientdata": json.dumps(clientdata)})

    reactivated = False
    if was_active:
        try:
            _dataverse("PATCH", base, headers=headers, json={"statecode": 1, "statuscode": 2})
            reactivated = True
        except PowerAutomateError:
            pass  # the definition IS saved; report the flow as left off rather than fail

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
