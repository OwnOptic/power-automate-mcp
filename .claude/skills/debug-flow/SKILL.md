---
name: debug-flow
description: Diagnose a failing Power Automate flow with the power-automate MCP tools. Use when a flow run failed, a flow that worked yesterday broke, failures are intermittent, or the user asks why their flow is not working. Walks the loop from finding the failed run to attributing the cause and verifying the fix.
---

# Debug a Power Automate flow

Work the loop in order. Skip a step only when you already hold its output.

1. **Resolve the flow.** `list_flows`, match on `display_name`, keep the `flow_id`.
2. **Find the failure.** `list_runs(flow_id, status="Failed")`. Newest first.
3. **Diagnose one run.** `explain_run(flow_id, run_id)` on the newest failed run.
   - `failed_actions[].error` is the real error text, already resolved from the
     SAS-signed blob. Do not go fetch it yourself.
   - `failed_actions` empty while status is Failed means the TRIGGER failed.
     `get_flow` and inspect the trigger's condition and inputs.
   - The cause is usually upstream: read the `succeeded` outputs for the value
     that fed the failing action.
4. **Error clear but cause not** ("it worked yesterday", intermittent,
   data-dependent): `compare_runs(flow_id, failed_run_id)`. It auto-picks the most
   recent Succeeded run as baseline. Read `diverged_at` first, then
   `output_changes` - an action that succeeded in both runs with different output
   is usually the real cause. `only_in_failed` / `only_in_baseline` means a
   condition or branch evaluated differently.
5. **Recurring or flaky:** `analyze_flow_health(flow_id)`. Trust the `verdict`:
   - broken (near-100% failure) - fix the definition, stop rerunning it
   - flaky, concentrated in one action - targeted fix on that action
   - flaky, spread across actions - suspect trigger data or a connection, not logic
6. **Fix and verify.** `get_flow` -> edit the returned `definition` ->
   `update_flow_definition` with the COMPLETE definition (there is no partial
   update). Re-run with `run_flow`, then `explain_run` again to confirm.

## Rules learned the hard way

- SAS URLs on old runs expire. If `explain_run` reports the error blob
  unavailable, re-run the flow and diagnose the fresh run instead of digging.
- `run_flow` does not forward a body: `@triggerBody()` is null when triggered
  through the management API. If the failure only reproduces with a real payload,
  call the flow's actual HTTP trigger URL.
- Deterministic vs data-dependent: the same action failing in both compared runs
  with empty `output_changes` means the definition is wrong. Different upstream
  outputs mean the data is.
