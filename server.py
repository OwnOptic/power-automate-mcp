"""pa-demo-mcp - a minimal Power Automate MCP server, built for a community call.

Seven tools. One file. No framework beyond the official MCP SDK.

The point of this server is NOT to be complete - the real one I run daily has 21
Power Automate tools. The point is to show the four layers every useful MCP has,
and to show that the value is in layers 3 and 4, not layer 1:

    1. AUTH        get a token for the API                     (~40 lines, boring)
    2. TRANSPORT   call the API, retry, paginate                (~50 lines, boring)
    3. SHAPING     turn API JSON into model-readable JSON       (this is the work)
    4. DOCSTRINGS  teach the model what the API will not        (this is the moat)

Layer 1 and 2 an LLM writes for you in one prompt. Layer 3 and 4 are where you
encode what you learned the hard way, and they are why your own MCP beats a
generic HTTP tool.

The star of the show is `explain_run`. See its docstring.

API: https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple
Docs: https://learn.microsoft.com/connectors/ and the Logic Apps workflow
definition language reference (Power Automate uses the same schema).
"""

from __future__ import annotations

import atexit
import os
import time
from pathlib import Path
from typing import Any

import httpx
import msal
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

mcp = FastMCP("pa-demo")

# ---------------------------------------------------------------------------
# LAYER 1 - AUTH
# Delegated device-code auth. Runs once, then MSAL caches the refresh token to
# disk and you are silent for ~90 days. Nothing here is Power Automate specific;
# swap the scope and this is a Graph client, or a Dataverse client.
# ---------------------------------------------------------------------------

CLIENT_ID = os.environ["PA_CLIENT_ID"]          # your app registration (public client)
TENANT_ID = os.environ["PA_TENANT_ID"]
ENV_ID = os.environ.get("PA_ENV_ID") or f"Default-{TENANT_ID}"

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://service.flow.microsoft.com/.default"]
CACHE_PATH = HERE / ".token_cache.json"

_cache = msal.SerializableTokenCache()
if CACHE_PATH.exists():
    _cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
atexit.register(
    lambda: CACHE_PATH.write_text(_cache.serialize(), encoding="utf-8")
    if _cache.has_state_changed
    else None
)

_app: msal.PublicClientApplication | None = None


def _client() -> msal.PublicClientApplication:
    """Build the MSAL client on first use, not at import.

    MSAL hits the tenant's OIDC discovery endpoint when you construct it. Doing
    that at import time means a wrong tenant id or dropped wifi kills the server
    before it registers a single tool, and the client reports nothing more useful
    than "server failed to start". Lazily, the same problem surfaces as a readable
    error inside a tool result.
    """
    global _app
    if _app is None:
        _app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=_cache)
    return _app


def _token(force: bool = False) -> str:
    """Return a bearer token, refreshing silently when possible."""
    app = _client()
    accounts = app.get_accounts()
    if accounts and not force:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # First run only: print a code, user pastes it at microsoft.com/devicelogin.
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"device flow failed: {flow.get('error_description')}")
    print(flow["message"], flush=True)  # visible in the MCP server log
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"auth failed: {result.get('error_description')}")
    return result["access_token"]


# ---------------------------------------------------------------------------
# LAYER 2 - TRANSPORT
# One request helper. Retries 401 once with a fresh token, backs off on 429/5xx,
# and follows nextLink for list endpoints. This is the whole HTTP story.
# ---------------------------------------------------------------------------

BASE = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple"
API_VERSION = "2016-11-01"
_http = httpx.Client(timeout=60)


def _call(method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
    url = path if path.startswith("http") else f"{BASE}{path}"
    params = {**(params or {}), "api-version": API_VERSION}
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

    for attempt in range(4):
        r = _http.request(method, url, params=params, json=body, headers=headers)

        if r.status_code < 300:
            return r.json() if r.content else {"status": "ok"}
        if r.status_code == 401 and attempt == 0:
            headers["Authorization"] = f"Bearer {_token(force=True)}"
            continue
        if r.status_code in (429, 503, 504) and attempt < 3:
            time.sleep(float(r.headers.get("Retry-After", 2**attempt)))
            continue

        # Terminal. Surface the API's own message - the model can often act on it.
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:400]
        except ValueError:
            msg = r.text[:400]
        raise RuntimeError(f"Power Automate API {r.status_code}: {msg}")

    raise RuntimeError(f"retries exhausted: {method} {path}")


def _list(path: str, *, params: dict | None = None, cap: int = 50) -> list[dict]:
    """GET a collection endpoint, following nextLink up to `cap` items."""
    items: list[dict] = []
    url, current = path, params
    while url and len(items) < cap:
        page = _call("GET", url, params=current)
        items.extend(page.get("value", []))
        url, current = page.get("nextLink"), None
    return items[:cap]


# ---------------------------------------------------------------------------
# LAYER 3 - SHAPING
# The Power Automate API returns ~60 fields per flow and ~40 per action, most of
# them GUIDs and internal plumbing. Handing that to a model burns context and
# buries the signal. Every tool below returns a hand-picked subset.
#
# Rule of thumb: if you would not read the field while debugging, the model does
# not need it either.
# ---------------------------------------------------------------------------

MAX_FIELD_CHARS = 2000


def _trim(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    return text[:MAX_FIELD_CHARS] + " ... [truncated]" if len(text) > MAX_FIELD_CHARS else value


def _flow_summary(raw: dict, with_definition: bool = False) -> dict:
    props = raw.get("properties", {})
    out = {
        "flow_id": raw.get("name"),
        "display_name": props.get("displayName"),
        "state": props.get("state"),
        "modified": props.get("lastModifiedTime"),
    }
    if with_definition:
        definition = props.get("definition") or {}
        out["triggers"] = list(definition.get("triggers", {}))
        out["actions"] = list(definition.get("actions", {}))
        out["definition"] = definition
    return out


def _run_summary(raw: dict) -> dict:
    props = raw.get("properties", {})
    return {
        "run_id": raw.get("name"),
        "status": props.get("status"),
        "start_time": props.get("startTime"),
        "end_time": props.get("endTime"),
        "error": props.get("error"),
    }


def _resolve_error(props: dict) -> str | None:
    """Fetch the real error text for a failed action.

    THE GOTCHA THIS WHOLE SERVER EXISTS FOR:
    Power Automate does NOT put the error message on the action record. The
    action comes back with status "Failed" and `error: null`. The actual message
    lives inside a blob behind a short-lived SAS URL in `outputsLink.uri`.

    The portal fetches that blob for you, which is why the portal shows you a
    real error and a naive API wrapper shows you "Failed" and nothing else.
    So the tool does the second hop itself. One extra HTTP call here saves the
    model three tool calls and a guess.
    """
    if props.get("error"):
        return props["error"].get("message") if isinstance(props["error"], dict) else str(props["error"])

    link = props.get("outputsLink") or {}
    if not isinstance(link, dict) or "uri" not in link:
        return None
    try:
        body = httpx.get(link["uri"], timeout=10).json()
    except Exception:  # noqa: BLE001 - SAS URLs expire; never let this break the tool
        return "[error blob unavailable - the SAS URL has expired, re-run the flow]"
    return (
        body.get("body", {}).get("error", {}).get("message")
        or body.get("error", {}).get("message")
        or str(body)[:MAX_FIELD_CHARS]
    )


# ---------------------------------------------------------------------------
# LAYER 4 - TOOLS
# Seven tools covering one loop: see -> build -> run -> fail -> understand -> fix.
#
# The docstrings are not documentation. They are the prompt. Everything the model
# gets wrong twice should end up written down here, permanently.
# ---------------------------------------------------------------------------


@mcp.tool()
def list_flows(state: str = "", top: int = 25) -> list[dict]:
    """List flows in the environment, newest change first.

    state filters client-side on 'Started' or 'Stopped'; leave empty for all.
    Returns flow_id, display_name, state, modified. Use flow_id with every other tool.
    """
    flows = [_flow_summary(f) for f in _list(f"/environments/{ENV_ID}/flows", cap=top)]
    return [f for f in flows if not state or f["state"] == state]


@mcp.tool()
def get_flow(flow_id: str) -> dict:
    """Get one flow with its full definition - the JSON behind the designer's Code view.

    Returns the trigger names, action names, and the complete definition dict.
    Call this before update_flow_definition: the API has no partial update, so you
    must send the whole definition back, modified.
    """
    raw = _call("GET", f"/environments/{ENV_ID}/flows/{flow_id}")
    return _flow_summary(raw, with_definition=True)


@mcp.tool()
def create_flow(display_name: str, definition: dict, start: bool = True) -> dict:
    """Create a flow from a workflow-definition dict.

    `definition` is the same JSON the designer shows under Code view. It needs
    at least `triggers` and `actions`.

    TWO RULES LEARNED THE HARD WAY:

    1. Declare the magic parameters. If any trigger or action uses a connector
       (type OpenApiConnection / OpenApiConnectionWebhook / ...Notification), the
       definition must carry, at top level next to triggers and actions:
           "parameters": {
             "$connections":    {"defaultValue": {}, "type": "Object"},
             "$authentication": {"defaultValue": {}, "type": "SecureObject"}
           }
       Without them creation 400s complaining that the TRIGGER is missing
       '$authentication', which sends you hunting in the wrong place. Harmless to
       include on connector-free flows, so just always include it.

    2. Creating does not bind connections. A 201 gives you a flow whose
       connectionReferences is {} and which will not start
       ("CannotStartUnpublishedSolutionFlow"). For connector flows the working
       sequence is create(start=False) -> update_flow_definition(with
       connection_references) -> a start call. Connector-free flows (Recurrence,
       Request, Compose, HTTP) start immediately, which is why the demo flow uses
       those.

    Never inline a secret in a definition - it is stored in plaintext on the flow
    artifact. Use a Power Platform environment variable or Key Vault reference.

    Look up connector operationIds and parameter schemas on Microsoft Learn rather
    than guessing them. Guessing produces a flow that saves fine and fails at runtime.
    """
    body = {"properties": {"displayName": display_name, "state": "Started" if start else "Stopped", "definition": definition}}
    return _flow_summary(_call("POST", f"/environments/{ENV_ID}/flows", body=body))


@mcp.tool()
def update_flow_definition(flow_id: str, definition: dict, connection_references: dict | None = None) -> dict:
    """Replace a flow's definition. Send the COMPLETE definition - there is no patch semantics.

    Standard use: get_flow -> edit the returned `definition` -> pass it here.

    `connection_references` is required for connector flows, shaped like
    {"shared_office365": {"connectionName": "shared-office365-<guid>",
                          "source": "Embedded",
                          "id": "/providers/Microsoft.PowerApps/apis/shared_office365"}}.

    LIMITATION: flows whose connections were bound in the maker portal become
    Dataverse connection references and cannot be updated through this endpoint
    (get_flow reports the host as `connectionName`, the PATCH wants
    `connectionReferenceName`, and the reference itself cannot be minted here).
    Edit those in the portal.
    """
    props: dict[str, Any] = {"definition": definition}
    if connection_references:
        props["connectionReferences"] = connection_references
    return _flow_summary(_call("PATCH", f"/environments/{ENV_ID}/flows/{flow_id}", body={"properties": props}))


@mcp.tool()
def run_flow(flow_id: str, trigger_name: str = "manual", inputs: Any = None) -> dict:
    """Trigger a flow now, without waiting for its schedule. You must own the flow.

    `trigger_name` is the trigger's internal name from get_flow ('manual' for
    button flows, the recurrence trigger's name for scheduled ones).

    Note for Request/HTTP-trigger flows: this management endpoint does NOT forward
    a body, so @triggerBody() evaluates to null here. Call the flow's real HTTP URL
    if the flow depends on its payload.

    Returns immediately - the run is asynchronous. Poll list_runs for the outcome.
    """
    return _call("POST", f"/environments/{ENV_ID}/flows/{flow_id}/triggers/{trigger_name}/run", body=inputs)


@mcp.tool()
def list_runs(flow_id: str, status: str = "", top: int = 10) -> list[dict]:
    """List recent runs for a flow, newest first.

    status filter: 'Succeeded', 'Failed', 'Cancelled', 'Running'. Empty for all.
    Returns run_id, status, start_time, end_time. Feed a failed run_id to explain_run.
    """
    params: dict[str, Any] = {"$top": min(top, 100), "$orderby": "startTime desc"}
    if status:
        params["$filter"] = f"status eq '{status}'"
    runs = _list(f"/environments/{ENV_ID}/flows/{flow_id}/runs", params=params, cap=top)
    return [_run_summary(r) for r in runs]


@mcp.tool()
def explain_run(flow_id: str, run_id: str) -> dict:
    """Diagnose a run: which action failed, with what error, on what inputs.

    This is the tool the portal makes you click twelve times to replicate, and the
    one a generic HTTP wrapper cannot give you, for two reasons:

      1. The error is not on the action record. Failed actions come back with
         `error: null`; the message sits in a SAS-signed blob referenced by
         outputsLink.uri. This tool follows that link for every failed action.
      2. A failure is rarely explained by the failing action alone. The cause is
         usually in what an EARLIER action produced. So the response pairs the
         failed action with the outputs of the actions that succeeded before it.

    Returns:
      status          overall run status
      failed_actions  [{name, error, inputs}] - resolved error text, not null
      succeeded       [{name, outputs}] - upstream context, in execution order
      hint            where to look first

    If failed_actions is empty and status is Failed, the failure was in the
    trigger, not the body - check the trigger's condition and inputs.
    """
    run = _run_summary(_call("GET", f"/environments/{ENV_ID}/flows/{flow_id}/runs/{run_id}"))
    actions = _list(f"/environments/{ENV_ID}/flows/{flow_id}/runs/{run_id}/actions", cap=100)

    failed, succeeded = [], []
    for action in actions:
        props = action.get("properties", {})
        record = {"name": action.get("name"), "status": props.get("status")}
        if props.get("status") == "Failed":
            record["error"] = _resolve_error(props)
            record["inputs"] = _trim(props.get("inputsLink") or props.get("inputs"))
            failed.append(record)
        elif props.get("status") == "Succeeded":
            record["outputs"] = _trim(props.get("outputsLink") or props.get("outputs"))
            succeeded.append(record)

    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "started": run["start_time"],
        "failed_actions": failed,
        "succeeded": succeeded,
        "hint": (
            f"{failed[0]['name']} failed. Its error is resolved above; check the outputs of "
            "the succeeded actions for the value that caused it."
            if failed
            else "No action failed. If the run status is Failed, the trigger failed - inspect the trigger."
        ),
    }


if __name__ == "__main__":
    mcp.run()
