"""pa-demo-mcp - a minimal Power Automate MCP server, built for a community call.

Ten tools. One file. No framework beyond the official MCP SDK.

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

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

mcp = FastMCP("pa-demo")

# ---------------------------------------------------------------------------
# LAYER 1 - AUTH
# Borrow the Azure CLI's token. That is the entire auth story.
#
# You register nothing. The Azure CLI is itself a Microsoft first-party app that
# your tenant already trusts, so `az login` replaces the app registration, the
# admin consent and the device-code dance in one move.
#
# Nothing here is Power Automate specific. Change PA_RESOURCE and this is a Graph
# client, a Dataverse client, or an Azure Resource Manager client. When you build
# an MCP server against any Azure-fronted API, try this before the Entra portal.
#
# The trade-off, worth knowing: the CLI's session lives under your tenant's
# Conditional Access policy, so it can expire mid-session. You get AADSTS70043
# and `az login` fixes it.
# ---------------------------------------------------------------------------

PA_RESOURCE = "https://service.flow.microsoft.com"


def _az(args: list[str]) -> str:
    """Run an Azure CLI command and return stdout.

    shell=True because on Windows `az` is a .cmd shim that CreateProcess will not
    resolve on its own. Every argument here is an internal constant, never
    user-supplied free text.
    """
    cmd = "az " + " ".join(f'"{a}"' if " " in a else a for a in args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, shell=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()[:400]
        raise RuntimeError(f"Azure CLI call failed ({cmd}): {err}\nRun `az login` first.")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"Azure CLI returned nothing for: {cmd}")
    return out


_token_cache: tuple[str, float] | None = None


def _token(force: bool = False) -> str:
    """A Flow-audience bearer token, cached in-process.

    The CLI holds its own refresh token, so this is cheap after the first call.
    The 45-minute cache avoids shelling out on every request, and the 401 retry in
    _call forces a refresh if the token dies sooner than expected.
    """
    global _token_cache
    if _token_cache and not force and _token_cache[1] - time.time() > 120:
        return _token_cache[0]
    token = _az(["account", "get-access-token", "--resource", PA_RESOURCE,
                 "--query", "accessToken", "-o", "tsv"])
    _token_cache = (token, time.time() + 45 * 60)
    return token


_env_id: str | None = None


def env_id() -> str:
    """The environment to operate on, resolved once.

    PA_ENV_ID wins. Otherwise the default environment of whatever tenant the Azure
    CLI is signed in to. Resolved lazily so the server still starts and registers
    its tools when az is not logged in - the failure then surfaces as a readable
    tool error rather than a server that will not come up.
    """
    global _env_id
    if _env_id is None:
        explicit = os.environ.get("PA_ENV_ID", "").strip()
        if explicit:
            _env_id = explicit
        else:
            tenant = _az(["account", "show", "--query", "tenantId", "-o", "tsv"])
            _env_id = f"Default-{tenant}"
    return _env_id


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


def _parse_time(value: str | None) -> datetime | None:
    """Parse a Power Automate timestamp.

    Not as trivial as it looks. PA emits .NET "round-trip" timestamps with SEVEN
    fractional digits (2026-08-04T14:22:01.1234567Z). On Python 3.10 and earlier
    datetime.fromisoformat accepts neither the trailing Z nor a 7-digit fraction,
    so the obvious one-liner raises ValueError on real data and duration stats
    silently come back empty. Python 3.11 relaxed both, but this project supports
    3.10, so normalise explicitly rather than depending on the runtime version.

    Returns None rather than raising: a stats helper must never take down the tool.
    """
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for char in tail:
            if not char.isdigit():
                break
            digits += char
        offset = tail[len(digits) :]  # whatever followed the fraction, e.g. +00:00
        text = f"{head}.{digits[:6].ljust(6, '0')}{offset}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    a, b = _parse_time(start), _parse_time(end)
    return (b - a).total_seconds() if a and b else None


def _discover_connections(max_flows: int = 60) -> dict[str, dict]:
    """Union the connections referenced by the environment's flows, keyed by connection id.

    IN-USE ONLY, and that is a limitation of the API, not a shortcut.

    There is no environment-wide connections route reachable with a Flow token:
    /environments/{env}/connections returns 404 under BOTH the Microsoft.ProcessSimple
    and Microsoft.PowerApps providers. The real route lives on a different host and
    audience entirely -
        GET https://api.powerapps.com/providers/Microsoft.PowerApps/environments/{env}/connections
    - and calling it with a service.flow.microsoft.com token returns 403 InvalidPath.
    You would need an aud=service.powerapps.com token, i.e. a second app registration
    and a second consent, to enumerate connections properly.

    The per-flow route DOES work, so this walks flows and unions what they reference.
    Consequence: a connection no flow uses yet is invisible here. For the question that
    actually matters - "is connector X already connected in this environment, and what
    is its connectionName?" - any bindable connection is normally on at least one flow.

    Costs one request per flow, hence max_flows.
    """
    flows = _list(f"/environments/{env_id()}/flows", params={"$top": max_flows}, cap=max_flows)
    found: dict[str, dict] = {}

    for flow in flows:
        flow_id = flow.get("name")
        if not flow_id:
            continue
        try:
            raw = _call("GET", f"/environments/{env_id()}/flows/{flow_id}/connections")
        except RuntimeError:
            continue  # one unreadable flow must not sink the whole scan
        items = raw if isinstance(raw, list) else raw.get("value", [])
        flow_name = flow.get("properties", {}).get("displayName") or flow_id

        for conn in items:
            props = conn.get("properties", {}) or {}
            conn_id = conn.get("name") or str(conn.get("id", "")).rsplit("/", 1)[-1]
            if not conn_id:
                continue
            entry = found.setdefault(
                conn_id,
                {
                    "connection_name": conn_id,
                    "display_name": props.get("displayName"),
                    "connector": (props.get("apiId") or "").split("/")[-1],
                    "status": (props.get("statuses") or [{}])[0].get("status"),
                    "used_by_flows": [],
                },
            )
            entry["used_by_flows"].append(flow_name)

    return found


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
    flows = [_flow_summary(f) for f in _list(f"/environments/{env_id()}/flows", cap=top)]
    return [f for f in flows if not state or f["state"] == state]


@mcp.tool()
def get_flow(flow_id: str) -> dict:
    """Get one flow with its full definition - the JSON behind the designer's Code view.

    Returns the trigger names, action names, and the complete definition dict.
    Call this before update_flow_definition: the API has no partial update, so you
    must send the whole definition back, modified.
    """
    raw = _call("GET", f"/environments/{env_id()}/flows/{flow_id}")
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
    return _flow_summary(_call("POST", f"/environments/{env_id()}/flows", body=body))


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
    return _flow_summary(_call("PATCH", f"/environments/{env_id()}/flows/{flow_id}", body={"properties": props}))


@mcp.tool()
def bind_connection(
    flow_id: str,
    connector: str,
    connection_name: str = "",
    start: bool = True,
) -> dict:
    """Bind an existing connection to a flow and start it. The missing step of create_flow.

    This is the tool that makes headless connector-flow creation actually work.
    `create_flow` returns 201 but leaves connectionReferences empty, and the flow
    cannot be started ("CannotStartUnpublishedSolutionFlow"). Binding is a separate
    PATCH that has to carry both the full definition AND the connection reference.
    This tool does the whole three-step sequence in one call:

        1. resolve the connection      (which connectionName for this connector?)
        2. PATCH definition + reference
        3. start the flow

    `connector` is the connector's logical name, e.g. "shared_office365",
    "shared_teams", "shared_sharepointonline".

    `connection_name` is the concrete connection id, e.g. "shared-office365-8f3a...".
    Leave it empty and the tool resolves it automatically. If the environment has
    several connections for that connector, it does NOT guess: it returns
    status "ambiguous" with the candidates so you can pass one explicitly.

    THE CONNECTION MUST ALREADY EXIST in the environment. No API reachable with a
    Flow token can create and authenticate a brand new connection - that is a portal
    action. If none is found this returns status "not_found" rather than failing.

    Does NOT work on solution or portal-bound flows: their connections are Dataverse
    connection references which cannot be minted here. Edit those in the portal.
    """
    candidates = (
        {connection_name: {"connection_name": connection_name, "connector": connector}}
        if connection_name
        else {
            cid: c
            for cid, c in _discover_connections().items()
            if c["connector"] == connector
        }
    )

    if not candidates:
        return {
            "status": "not_found",
            "connector": connector,
            "message": (
                f"No connection for '{connector}' is referenced by any flow in this "
                "environment. Either it does not exist yet (create and authenticate it "
                "in the maker portal - no API here can do that), or it exists but no "
                "flow uses it yet, which makes it invisible to connection discovery."
            ),
        }
    if len(candidates) > 1:
        return {
            "status": "ambiguous",
            "connector": connector,
            "candidates": list(candidates.values()),
            "message": "Several connections match. Re-call with connection_name set to one of these.",
        }

    resolved = next(iter(candidates))
    definition = _call("GET", f"/environments/{env_id()}/flows/{flow_id}")["properties"]["definition"]

    _call(
        "PATCH",
        f"/environments/{env_id()}/flows/{flow_id}",
        body={
            "properties": {
                "definition": definition,
                "connectionReferences": {
                    connector: {
                        "connectionName": resolved,
                        "source": "Embedded",
                        "id": f"/providers/Microsoft.PowerApps/apis/{connector}",
                    }
                },
            }
        },
    )

    started = None
    if start:
        try:
            _call("POST", f"/environments/{env_id()}/flows/{flow_id}/start")
            started = True
        except RuntimeError as exc:
            started = f"bound, but start failed: {exc}"

    bound = _call("GET", f"/environments/{env_id()}/flows/{flow_id}/connections")
    bound_count = len(bound if isinstance(bound, list) else bound.get("value", []))

    return {
        "status": "bound",
        "flow_id": flow_id,
        "connector": connector,
        "connection_name": resolved,
        "connections_on_flow": bound_count,
        "started": started,
        "hint": "connections_on_flow should be >= 1. If it is 0 the flow is portal-bound and must be edited in the portal.",
    }


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
    return _call("POST", f"/environments/{env_id()}/flows/{flow_id}/triggers/{trigger_name}/run", body=inputs)


@mcp.tool()
def list_runs(flow_id: str, status: str = "", top: int = 10) -> list[dict]:
    """List recent runs for a flow, newest first.

    status filter: 'Succeeded', 'Failed', 'Cancelled', 'Running'. Empty for all.
    Returns run_id, status, start_time, end_time. Feed a failed run_id to explain_run.
    """
    params: dict[str, Any] = {"$top": min(top, 100), "$orderby": "startTime desc"}
    if status:
        params["$filter"] = f"status eq '{status}'"
    runs = _list(f"/environments/{env_id()}/flows/{flow_id}/runs", params=params, cap=top)
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
    run = _run_summary(_call("GET", f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}"))
    actions = _list(f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}/actions", cap=100)

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


@mcp.tool()
def compare_runs(flow_id: str, failed_run_id: str, baseline_run_id: str = "") -> dict:
    """Diff a failed run against a working one to find what changed.

    Use this when explain_run gives you an error that is technically clear but does
    not tell you WHY this run differed - intermittent failures, "it worked yesterday",
    data-dependent bugs. The portal makes you open two runs in two tabs and eyeball
    them side by side.

    `baseline_run_id` is optional. Left empty, the tool finds the most recent
    Succeeded run of the same flow and uses that.

    Returns:
      diverged_at      first action whose status differs - start here
      status_changes   [{action, baseline, failed}] every action that behaved differently
      output_changes   [{action, baseline, failed}] actions that succeeded in BOTH runs
                       but produced different outputs. This is usually the real cause.
      only_in_failed / only_in_baseline
                       actions that ran in one and not the other, which means a
                       condition or branch evaluated differently

    If output_changes is empty and status_changes shows the same action failing both
    times, the failure is deterministic and the fix is in the definition, not the data.
    """
    if not baseline_run_id:
        succeeded = list_runs(flow_id, status="Succeeded", top=1)
        if not succeeded:
            return {
                "status": "no_baseline",
                "message": "This flow has no Succeeded run to compare against. Use explain_run instead.",
            }
        baseline_run_id = succeeded[0]["run_id"]

    def actions_of(run_id: str) -> dict[str, dict]:
        raw = _list(f"/environments/{env_id()}/flows/{flow_id}/runs/{run_id}/actions", cap=100)
        return {a.get("name"): a.get("properties", {}) for a in raw}

    failed, baseline = actions_of(failed_run_id), actions_of(baseline_run_id)

    status_changes, output_changes = [], []
    for name in [n for n in failed if n in baseline]:
        f_status = failed[name].get("status")
        b_status = baseline[name].get("status")
        if f_status != b_status:
            status_changes.append({"action": name, "baseline": b_status, "failed": f_status})
        elif f_status == "Succeeded":
            f_out = failed[name].get("outputsLink") or failed[name].get("outputs")
            b_out = baseline[name].get("outputsLink") or baseline[name].get("outputs")
            # outputsLink URIs differ per run by design; compare content size instead.
            f_key = f_out.get("contentSize") if isinstance(f_out, dict) and "uri" in f_out else f_out
            b_key = b_out.get("contentSize") if isinstance(b_out, dict) and "uri" in b_out else b_out
            if f_key != b_key:
                output_changes.append(
                    {"action": name, "baseline": _trim(b_key), "failed": _trim(f_key)}
                )

    return {
        "failed_run": failed_run_id,
        "baseline_run": baseline_run_id,
        "diverged_at": status_changes[0]["action"] if status_changes else None,
        "status_changes": status_changes,
        "output_changes": output_changes,
        "only_in_failed": [n for n in failed if n not in baseline],
        "only_in_baseline": [n for n in baseline if n not in failed],
    }


@mcp.tool()
def analyze_flow_health(flow_id: str, last_n: int = 50, sample_failures: int = 5) -> dict:
    """Analyse a flow's recent run history: reliability, failure patterns, duration.

    Answers the questions a single run cannot: is this flow flaky or broken? Which
    action fails most? Is it getting slower? The portal's analytics page shows counts
    but will not tell you WHICH action is responsible or group failures by cause.

    `last_n` runs are analysed for statistics. Because action-level detail costs one
    request per run, only `sample_failures` of the failed runs are opened to attribute
    causes. The response says how many were sampled - do not read the tally as exhaustive.

    Returns:
      total / succeeded / failed / cancelled / running
      failure_rate     0.0 to 1.0 over completed runs only
      duration_seconds {mean, p95, max} across successful runs
      failing_actions  [{action, count, sample_error}] ranked, from the sampled failures
      verdict          plain-language read of the numbers

    A single action accounting for nearly all failures means a targeted fix. Failures
    spread across many actions usually means the trigger data or a connection, not the logic.
    """
    runs = list_runs(flow_id, top=last_n)
    if not runs:
        return {"status": "no_runs", "message": "This flow has never run."}

    tally = {"Succeeded": 0, "Failed": 0, "Cancelled": 0, "Running": 0}
    for run in runs:
        tally[run["status"]] = tally.get(run["status"], 0) + 1

    completed = tally["Succeeded"] + tally["Failed"]
    failure_rate = round(tally["Failed"] / completed, 3) if completed else 0.0

    durations = sorted(
        d
        for d in (
            _duration_seconds(r["start_time"], r["end_time"])
            for r in runs
            if r["status"] == "Succeeded"
        )
        if d is not None
    )
    duration_stats = (
        {
            "mean": round(sum(durations) / len(durations), 2),
            "p95": round(durations[min(int(len(durations) * 0.95), len(durations) - 1)], 2),
            "max": round(durations[-1], 2),
        }
        if durations
        else None
    )

    # Attribute failures. Bounded: one request per sampled run.
    failed_runs = [r for r in runs if r["status"] == "Failed"][:sample_failures]
    causes: dict[str, dict] = {}
    for run in failed_runs:
        for action in _list(
            f"/environments/{env_id()}/flows/{flow_id}/runs/{run['run_id']}/actions", cap=100
        ):
            props = action.get("properties", {})
            if props.get("status") != "Failed":
                continue
            name = action.get("name")
            entry = causes.setdefault(name, {"action": name, "count": 0, "sample_error": None})
            entry["count"] += 1
            if entry["sample_error"] is None:
                entry["sample_error"] = _resolve_error(props)

    ranked = sorted(causes.values(), key=lambda c: c["count"], reverse=True)

    if not completed:
        verdict = "No completed runs yet."
    elif failure_rate == 0:
        verdict = f"Healthy. {tally['Succeeded']} of {tally['Succeeded']} completed runs succeeded."
    elif failure_rate >= 0.9:
        verdict = f"Broken, not flaky: {int(failure_rate * 100)}% of runs fail. Fix the definition."
    elif ranked and ranked[0]["count"] >= max(1, len(failed_runs) * 0.8):
        verdict = f"Flaky, concentrated in '{ranked[0]['action']}' - that one action explains almost every failure."
    else:
        verdict = f"Flaky, {int(failure_rate * 100)}% failure rate, spread across actions. Suspect trigger data or a connection rather than logic."

    return {
        "flow_id": flow_id,
        "runs_analysed": len(runs),
        **tally,
        "failure_rate": failure_rate,
        "duration_seconds": duration_stats,
        "failures_sampled": len(failed_runs),
        "failing_actions": ranked,
        "verdict": verdict,
    }


if __name__ == "__main__":
    mcp.run()
