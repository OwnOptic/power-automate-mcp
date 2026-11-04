---
name: build-flow
description: Create or modify a Power Automate flow headlessly with the power-platform MCP tools. Use when asked to create a flow, add or change an action, bind a connector, or start a flow that will not start. Encodes the definition rules and the create-bind-start sequence that make headless authoring work.
---

# Build or modify a Power Automate flow

## Definition rules (violating any of these produces a misleading failure)

1. **Always declare the magic parameters** at top level next to `triggers` and
   `actions`:
   ```json
   "parameters": {
     "$connections":    {"defaultValue": {}, "type": "Object"},
     "$authentication": {"defaultValue": {}, "type": "SecureObject"}
   }
   ```
   Without them, connector flows 400 with an error blaming the trigger, which
   sends you hunting in the wrong place. Harmless on connector-free flows, so
   always include them.
2. **Look up connector operationIds and parameter keys on Microsoft Learn.**
   Never guess them - a guessed operationId saves fine and fails at runtime. Use
   the Microsoft Learn MCP server (`microsoft_docs_search`, `microsoft_docs_fetch`):
   - Fetch `https://learn.microsoft.com/connectors/<connector>/` once you know the
     connector (`shared_teams` -> `teams`). Searching the bare operationId tends
     to land on SDK pages instead.
   - Connector pages are large (Teams is ~157k characters). Search first; fetch
     only when the excerpt is not enough.
   - Skip anything marked `[DEPRECATED]` and use the replacement it names.
   - A parameter typed `dynamic` (e.g. Teams `body`) stops at the top-level key.
     Take the nested shape from `get_flow` on a working flow - rule 4.
   If the Learn server is not connected, say so rather than guessing.
3. **Never inline a secret in a definition.** It is stored in plaintext on the
   flow artifact. Use a Power Platform environment variable or a Key Vault
   reference.
4. **Start from a working example.** `get_flow` on an existing similar flow and
   adapt its definition rather than writing one from scratch.

## Create

- Connector-free (Recurrence, Request, Compose, HTTP actions):
  `create_flow(display_name, definition)` - it starts immediately.
- Connector flow: `create_flow(..., start=False)`, then
  `bind_connection(flow_id, connector)`. Creating never binds connections; a 201
  still leaves the flow unstartable ("CannotStartUnpublishedSolutionFlow").
  `bind_connection` resolves the connectionName, PATCHes definition + reference,
  and starts the flow in one call.
  - `status: "ambiguous"` - re-call with one of the listed `connection_name`s.
  - `status: "not_found"` - the connection must first be created and
    authenticated in the maker portal. No API reachable here can do that.

## Modify

- `get_flow` -> edit the returned `definition` -> `update_flow_definition` with
  the COMPLETE definition. There is no patch semantics; partial definitions
  delete what they omit.
- Connector flows: leave `connection_references` out. The tool reads the flow's
  live bindings and carries them over; `get_flow` shows `{}` for them, so there is
  nothing to copy. Pass it only to change a binding.
- Keep `previous_definition` from the result until the change is verified. There is
  no undo endpoint; that is the undo.
- Solution flows cannot be updated through this API (their connections are
  Dataverse connection references). With `extras.py` connected, use
  `get_solution_flow_clientdata` -> edit -> `update_solution_flow_definition`;
  without it, edit them in the portal. `list_solutions` says whether a flow's
  solution is managed - managed means read-only here, change the source instead.
  Flows built in the portal under My flows are NOT solution flows and update fine.

## Verify (close the loop after every change)

`run_flow` -> `list_runs` -> `explain_run` if it failed. Remember `run_flow`
sends no body, so `@triggerBody()` is null; flows that depend on their payload
need their real HTTP trigger URL called instead (`get_trigger_url` in `extras.py`),
or, to retest on data that already failed, `resubmit_run`.

If a flow will not start after a change, `set_flow_state` reports `ok: false`
rather than pretending: an unbound connector is the usual cause, and
`bind_connection` is the fix.
