---
name: agent-tool
description: Give a Copilot Studio agent a new capability by building a Power Automate flow it can call, plugging it in as a tool, updating the agent's instructions to use it, and publishing. Use when asked to add a tool, action or flow to an agent, connect an agent to data, update an agent's instructions, or "make the agent able to ...".
---

# Give an agent a tool it can call

Uses the `power-platform-copilot` server (copilot_studio.py). The loop, in order:

1. **Find the agent.** `list_agents`, then `get_agent` on it. Read its current
   instructions and tools aloud before changing anything; keep them for the undo.

2. **Build the flow as an AGENT flow.** `create_agent_flow`, not `create_flow`.
   Copilot Studio only calls solution flows, and `create_flow` makes a My flows flow the
   agent cannot reach. The definition needs:
   - trigger `{"type": "Request", "kind": "Skills"}`, inputs in `inputs.schema.properties`,
     each with a `description` the agent will read to fill it;
   - a `{"type": "Response", "kind": "Skills"}` action ("Respond to the agent"), outputs
     in `inputs.schema.properties`, values in `inputs.body`.
   Start from `demo-agent-flow.json`. Keep it under 100 seconds: the agent waits for it.
   Pass `solution` to keep the flow with the agent's solution for export.

3. **Plug it in.** `add_flow_tool` with a `description` that says what the tool does,
   what it needs and what it returns. That sentence is how the agent decides when to call
   it: write it like a brief to a colleague, not a label.

4. **Tell the agent.** `update_agent_instructions` with the COMPLETE new text: when to
   call the tool, how to phrase the answer, what language. Name the tool by its display
   name. No `{` or `}` (the tool refuses them; Copilot Studio parses them as Power Fx).

5. **Publish.** `publish_agent`. Only `Succeeded` means live. The Copilot Studio test
   pane sees drafts before that; Teams and Microsoft 365 Copilot do not.

6. **Prove it.** Ask the user to try one question in the test pane that should trigger
   the tool, then `list_runs` on the flow: a run means the agent called it, and
   `explain_run` shows what it sent and got back. No run means the agent did not choose
   the tool - sharpen the tool description or the instructions, not the flow.

## Undo

`update_agent_instructions` returns `previous_instructions`. A tool is a component of
the agent: remove it in Copilot Studio (Tools, the tool, Delete), or ask before deleting
it any other way. `delete_flow` (extras) removes the flow.
