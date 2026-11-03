"""Layer 3: the pure functions. Each test pins one gotcha the README documents."""

from __future__ import annotations

import copy

import server
from conftest import load


# --- _validate_definition: the API's rules, checked before the API sees them ------

def rules(definition: dict, refs: dict | None = None) -> set[str]:
    return {i["rule"] for i in server._validate_definition(definition, refs)}


def test_demo_flows_pass_validation():
    assert not [i for i in server._validate_definition(load("demo-flow.json")) if i["severity"] == "error"]
    assert not [i for i in server._validate_definition(load("demo-flow-connector.json")) if i["severity"] == "error"]


def test_missing_magic_parameters_are_both_reported():
    # Gotcha 2: the API names the trigger and only one parameter at a time.
    definition = load("demo-flow.json")
    del definition["parameters"]
    assert {"missing-authentication-param", "missing-connections-param"} <= rules(definition)


def test_dangling_run_after_is_an_error():
    definition = load("demo-flow.json")
    definition["actions"]["Compute_batches"]["runAfter"] = {"Load_setings": ["Succeeded"]}
    assert "dangling-run-after" in rules(definition)


def test_approval_needs_the_webhook_type():
    definition = load("demo-flow.json")
    definition["actions"]["Approve"] = {
        "type": "OpenApiConnection",
        "inputs": {"host": {"operationId": "StartAndWaitForAnApproval", "connectionName": "shared_approvals"}},
    }
    assert "webhook-type-mismatch" in rules(definition)


def test_invoker_connection_source_is_rejected():
    definition = load("demo-flow-connector.json")
    refs = {"shared_teams": {"connectionName": "abc", "source": "Invoker", "id": "/providers/x/apis/shared_teams"}}
    assert "invoker-connection" in rules(definition, refs)
    refs["shared_teams"]["source"] = "Embedded"
    assert "invoker-connection" not in rules(definition, refs)


def test_http_request_trigger_warns_about_premium():
    definition = load("demo-flow.json")
    definition["triggers"]["manual"]["kind"] = "Http"
    issues = server._validate_definition(definition)
    assert any(i["rule"] == "premium-trigger" and i["severity"] == "warning" for i in issues)


def test_raise_on_errors_blocks_and_passes_warnings_through():
    issues = [
        {"severity": "warning", "rule": "w", "message": "m", "path": ""},
    ]
    assert server._raise_on_errors(issues) == issues
    issues.append({"severity": "error", "rule": "e", "message": "boom", "path": ""})
    try:
        server._raise_on_errors(issues)
    except RuntimeError as exc:
        assert "rejected locally" in str(exc) and "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


# --- _normalise_definition: what the designer needs and the API does not check ----

def test_button_schema_is_filled_so_the_designer_opens_it():
    definition = load("demo-flow.json")
    notes = server._normalise_definition(definition)
    schema = definition["triggers"]["manual"]["inputs"]["schema"]
    assert schema == {"type": "object", "properties": {}, "required": []}
    assert notes and notes[0]["rule"] == "button-schema-normalised"
    # Second pass is a no-op.
    assert server._normalise_definition(definition) == []


# --- _parse_time: seven fractional digits and a Z, on Python 3.10 -----------------

def test_parse_time_handles_dotnet_round_trip_format():
    parsed = server._parse_time("2026-08-04T14:22:01.1234567Z")
    assert parsed is not None and parsed.microsecond == 123456 and parsed.utcoffset().total_seconds() == 0


def test_parse_time_never_raises():
    assert server._parse_time(None) is None
    assert server._parse_time("not a time") is None


def test_duration_seconds():
    assert server._duration_seconds("2026-08-04T14:22:01Z", "2026-08-04T14:22:05.5Z") == 4.5
    assert server._duration_seconds("2026-08-04T14:22:01Z", None) is None


# --- _shape_payload: the body a person would read, not the HTTP envelope ----------

def test_shape_payload_drops_headers_and_annotations():
    raw = {
        "statusCode": 200,
        "headers": {"Set-Cookie": "x" * 1500},
        "body": {"value": [{"@odata.etag": "W/1", "name": "Contoso", "numberofemployees": 0,
                            "numberofemployees@OData.Community.Display.V1.FormattedValue": "0"}]},
    }
    assert server._shape_payload(raw) == {"value": [{"name": "Contoso", "numberofemployees": 0}]}


def test_shape_payload_keeps_a_failing_status_code():
    assert server._shape_payload({"statusCode": 404, "headers": {}, "body": {"error": "x"}}) == {
        "statusCode": 404, "body": {"error": "x"}}


def test_trim_caps_long_fields():
    assert server._trim("a" * 10) == "a" * 10
    assert server._trim("a" * 5000).endswith("[truncated]")


# --- _resolve_error / _resolve_content: the SAS second hop -------------------------

def test_inline_expression_error_needs_no_hop(blobs):
    props = {"status": "Failed", "error": {"code": "InvalidTemplate", "message": "divisor of zero"}}
    assert server._resolve_error(props) == "divisor of zero"


def test_connector_error_is_read_from_the_blob_and_prefers_inner_error(blobs):
    blobs({"https://blob/1": {"statusCode": 404, "body": {"error": {
        "code": "NotFound", "message": "NotFound",
        "innerError": {"message": "LocationLookupFailed-Location lookup failed for thread 19:x"}}}}})
    props = {"status": "Failed", "outputsLink": {"uri": "https://blob/1", "contentSize": 285}}
    assert server._resolve_error(props) == "404 NotFound: LocationLookupFailed-Location lookup failed for thread 19:x"


def test_code_is_dropped_when_it_only_repeats_the_message(blobs):
    blobs({"https://blob/2": {"statusCode": 404, "body": {"error": {"code": "NotFound", "message": "NotFound"}}}})
    assert server._resolve_error({"outputsLink": {"uri": "https://blob/2"}}) == "404: NotFound"


def test_expired_blob_degrades_to_a_readable_message(blobs):
    assert "expired" in server._resolve_error({"outputsLink": {"uri": "https://blob/gone"}})
    assert server._resolve_content({"uri": "https://blob/gone", "contentSize": 50})["contentSize"] == 50


def test_resolve_content_passes_plain_values_through(blobs):
    assert server._resolve_content({"batch_size": 0}) == {"batch_size": 0}
    blobs({"https://blob/3": {"batch_size": 4}})
    assert server._resolve_content({"uri": "https://blob/3"}) == {"batch_size": 4}


# --- connector detection: nested scopes count --------------------------------------

def test_connectors_are_found_inside_nested_blocks():
    connector = load("demo-flow-connector.json")["actions"]["Post_message"]
    definition = {
        "triggers": {"manual": {"type": "Request", "kind": "Button"}},
        "actions": {"Check": {"type": "If", "actions": {}, "else": {"actions": {"Post": copy.deepcopy(connector)}}}},
    }
    assert server._uses_connectors(definition)
    assert server._definition_connectors(definition) == {"shared_teams"}
    assert not server._uses_connectors(load("demo-flow.json"))
    assert server._definition_connectors(load("demo-flow.json")) == set()
