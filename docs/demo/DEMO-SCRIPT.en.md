# Demo script: Claude builds flows, fixes them, and teaches an agent to use them

**Length:** 30 minutes, live. **Audience:** colleagues who will install it afterwards.
**Video version:** [`docs/video/power-platform-mcp-en.mp4`](../video/power-platform-mcp-en.mp4) - play it if the tenant misbehaves.
**French version:** [`DEMO-SCRIPT.fr.md`](DEMO-SCRIPT.fr.md).

The sentence the demo has to land:

> The API tells you WHAT broke. Your own MCP server tells Claude WHY - and then Claude
> can fix it, wire it into an agent, and ship it in a solution.

Everything below was run end to end before it was written down. Timings are from that
rehearsal; add 20 to 40 seconds of Claude's own thinking per prompt.

---

## Pre-flight

### The day before (one hour, once)

**Tenant.** A demo or development tenant, never production, never a client. You need an
environment **with Dataverse** (Copilot Studio agents and solutions live there).

1. **One az profile for the demo tenant**, so your everyday `az` stays where it is:
   ```powershell
   $env:AZURE_CONFIG_DIR = "$HOME\.azure-demo"
   az login --tenant <demo tenant id>
   az account get-access-token --resource https://service.flow.microsoft.com --query expiresOn -o tsv
   ```
   A timestamp means it works.
2. **The repo**, in a folder you will demo from:
   ```powershell
   git clone https://github.com/OwnOptic/power-platform-mcp.git
   cd power-platform-mcp
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
3. **`.env`** next to `server.py`:
   ```ini
   PA_TENANT_ID=<demo tenant id>
   PA_ENV_ID=<environment id with Dataverse>
   ```
   The environment id is in the make.powerautomate.com URL after you switch to it.
4. **`.mcp.json`** in that folder - three local servers plus Microsoft Learn, all on the
   demo profile (absolute paths):
   ```json
   {
     "mcpServers": {
       "power-platform":         { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/server.py"],         "env": { "AZURE_CONFIG_DIR": "C:/Users/you/.azure-demo" } },
       "power-platform-extras":  { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/extras.py"],         "env": { "AZURE_CONFIG_DIR": "C:/Users/you/.azure-demo" } },
       "power-platform-copilot": { "command": "C:/demo/power-platform-mcp/.venv/Scripts/python.exe", "args": ["C:/demo/power-platform-mcp/copilot_studio.py"], "env": { "AZURE_CONFIG_DIR": "C:/Users/you/.azure-demo" } },
       "microsoft-learn": { "type": "http", "url": "https://learn.microsoft.com/api/mcp" }
     }
   }
   ```
5. **An agent to teach.** In Copilot Studio, in that environment, create an agent called
   **Store Helper** (blank, generative orchestration on, instructions empty). Publish it
   once. This is the one thing the tools do not create: an agent is born in the portal.
   Then a second one with the **new agent experience** (GitHub Copilot harness), called
   **Store Helper Next**, empty, not published. Leave whatever Work IQ access it is
   created with: Beat 2b removes it.
6. **pac for the solutions part.** `pac auth create --name demo --environment <org url>`
   (sign in in the browser). Then `pac auth list` and **note which profile is active** -
   select it back after the demo.
7. **Rehearse every beat once**, then run the reset at the bottom.

### Thirty minutes before

- [ ] Fresh Claude Code session **opened in the repo folder**. `/mcp` shows four servers, all connected.
- [ ] `az account get-access-token ...` from step 1 still prints a timestamp (Conditional Access can expire the CLI session; if you see AADSTS70043, `az login` now, not on stage).
- [ ] Browser tab 1: make.powerautomate.com on the demo environment. Tab 2: Copilot Studio, Store Helper, test pane open. Tab 3: Copilot Studio, Store Helper Next.
- [ ] `pac auth select --name demo`.
- [ ] Font size up in terminal and browser. Notifications off. Teams closed.

---

## Beat sheet

### 0:00 - 2:00 Hook: the clicks

**Screen:** Power Automate portal, run history of any failed run.

Click through it live, counting out loud: flow, run history, the failed run, expand the
action, inputs, outputs, then the earlier action to find the value that caused it.

> "Every one of us has done this. For the next half hour, Claude does the whole loop:
> build the flow, break it, tell me why, fix it, give it to an agent, and ship it in a
> solution. And at the end, every one of you installs it."

### 2:00 - 6:00 What it is, and how you install it

**Screen:** the README, then Claude Code. Type `/mcp`.

Four servers, three of them this repo, all local, all running as **you**:

- `power-platform` - ten tools: build, run, diagnose.
- `power-platform-extras` - environments, start and stop, cancel, resubmit, solutions.
- `power-platform-copilot` - Copilot Studio agents, on both harnesses: classic topics, or the new GitHub Copilot harness with its skills.
- `microsoft-learn` - Microsoft's own docs server, so Claude looks connector actions up
  instead of guessing.

> "No app registration, no secret. It borrows my `az login`. And it has its own az
> profile pinned to this tenant: my day-to-day az is signed in somewhere else, and this
> cannot touch it."

Show the install in four lines (pre-flight steps 2 to 4). Say it plainly: clone,
venv, `pip install -r requirements.txt`, add the `.mcp.json` block, restart Claude Code.
**Do not install live** - it is already installed; you are showing how short it is.

### 6:00 - 11:00 Beat 1: build, break, explain (`power-platform`)

**Prompt:**
```
Create a flow called "DEMO - nightly batch" from demo-flow.json, run it, and tell me what happened.
```

Expect `create_flow` (about 3 s), `run_flow`, `list_runs`: **Failed**. Then, often
unprompted, `explain_run`. If not:

```
Why did it fail?
```

Expect: `Compute_batches` failed, divide by zero, **`batch_size: 0` from `Load_settings`**.

> "The API gave me 'Failed'. The tool followed the links the portal follows and put the
> upstream value next to the error. That is where 'why' comes from: one call instead of
> the clicks from the start."

**Prompt:**
```
Fix it with a batch size of 4, run it again, then compare the failed run with the one that worked.
```

Expect `get_flow`, `update_flow_definition` (it keeps the previous definition - mention
it), `run_flow`, **Succeeded**, then `compare_runs`: diverged at `Compute_batches`, output
change `batch_size 0 -> 4`.

> "0 and 4 are the same number of bytes. A naive diff calls those two runs identical.
> That was a real bug, and the fix is written in the tool's docstring, forever."

**Fallback:** a 400 on create - read it aloud. A 400 the docstring predicted is a better
demo than a clean 201.

### 11:00 - 16:00 Beat 2: give the agent a tool (`power-platform-copilot`)

**Screen:** Copilot Studio, Store Helper. Show it is empty: no instructions, no tools.

**Prompt:**
```
Look at my Store Helper agent. Then build an agent flow called "DEMO - Order status" from demo-agent-flow.json, add it to Store Helper as a tool called "Get order status", update its instructions so it uses that tool to answer questions about orders in the language of the question, and publish it.
```

Expect, in order: `list_agents`, `get_agent`, `create_agent_flow`, `add_flow_tool`,
`update_agent_instructions`, `publish_agent` (about 35 s - talk through it):

1. **Why `create_agent_flow` and not `create_flow`.** Copilot Studio only calls solution
   flows. A flow made through the Power Automate API has no Dataverse row at all, so an
   agent tool pointing at it saves, even publishes, and fails in front of a user. The
   tool refuses that case with the reason.
2. **The tool description** is how the agent decides when to call it. Read it aloud.
3. **No braces in instructions.** Copilot Studio reads `{` as Power Fx; the tool refuses
   them before saving.

**Screen:** Copilot Studio. Refresh. The tool and the instructions are there. In the
test pane, ask:

```
Where is order SO-1043?
```

Expect: "Delayed at the warehouse, expected next Monday, DHL" (from the flow). Then ask
in French: `Et la commande SO-1042 ?` - same tool, French answer.

**Back in Claude Code:**
```
Show me the runs of the order status flow and explain the last one.
```

> "Here is the proof the agent called the flow: a run, with the order number the agent
> filled in from my question."

**Fallback:** the agent answers without calling the tool - say so, and run
`Make the tool description more explicit about order numbers and publish again.`
That is the honest lesson: the description is the prompt.

### 16:00 - 20:00 Beat 2b: the same work on the new harness (`power-platform-copilot`)

**Screen:** Copilot Studio, Store Helper Next. Point at the address bar: `.../agents/<id>`,
not `.../bots/<id>`.

> "Copilot Studio now builds agents on two runtimes. This one has no topics, it has
> skills, and it stores the agent somewhere else entirely. Same tools, same login."

**Prompt:**
```
Look at my Store Helper Next agent. Which harness is it on, and what does it have: instructions, skills, knowledge, Work IQ access?
```

Expect `get_agent`: harness `github_copilot`, no instructions, no skills, and often six
Work IQ capabilities at Write access (mail, calendar, files, Teams, people, sites).

> "Read that list out loud: an order assistant that can write my mail. Nobody asked for that."

**Prompt:**
```
Remove the Work IQ capabilities it does not need to answer order questions. Show me what you would remove first.
```

Expect `remove_agent_component` as a dry run first, the data shown as the backup, then
the removal once you say yes.

**Prompt:**
```
Write its instructions so it answers order questions in the language of the question, and add a skill called order-status that says how to answer when someone asks where an order is: status, expected date, carrier, one short paragraph.
```

Expect `update_agent_instructions` (on this harness it writes the agent's configuration:
there is no `gpt.default` component) and `upsert_agent_skill`.

**Screen:** Copilot Studio, refresh the agent: the instructions and the skill are there.

> "One trap: never keep a designer tab open while Claude writes. Its next save puts the
> old copy back."

**Prompt:**
```
Export Store Helper Next as if I had to rebuild it in another environment, and tell me what the export does not carry.
```

Expect `export_agent`: every component with its kind, and the honest gaps (uploaded
knowledge files have no bytes in Dataverse, and suggested prompts are not stored there).

**Do not chat with it on stage.** This harness bills Copilot Credits from the first test
message; the point of the beat is the build, not the conversation.

**Fallback:** the agent has no Work IQ capabilities: skip that prompt and say why it is
worth checking on every new agent.

### 20:00 - 25:00 Beat 3: skills, schemas and solutions (`pac`)

**Screen:** `.claude/skills/`. Open `solution-from-base/SKILL.md`.

> "Docstrings teach Claude one tool. Skills teach it a whole procedure - and a skill is
> just a markdown file with a description that says when to use it."

**Prompt:**
```
Using the dataverse-schema skill, create the schema in schema/orders.example.json in this environment. Show me the plan first.
```

Expect the plan (publisher, solution `PAMCPDemo`, table `pmd_order`, seven columns and a
lookup), then apply (about 1.5 min - talk over it: "every column is a metadata call, and
the script publishes at the end").

**Prompt:**
```
Using the solution-from-base skill, export PAMCPDemo with pac, unpack it, add a "Tracking number" text column to Order by copying an existing column, bump the version, pack it and import it back. Then prove the column exists.
```

Expect: `pac auth list` (it checks where it points before anything else), export, unpack,
an edit to `Entity.xml`, `Solution.xml` 1.0.0.0 -> 1.1.0.0, pack, import (about 2 min),
then a Dataverse check that `pmd_trackingnumber` exists.

> "This is the 'start from someone's solution' workflow. Drop a colleague's .zip in the
> folder and say 'use this as a base' - same skill, it skips the export."

**Fallback:** import is slow. Move on to beat 4 while it runs and come back to the proof.

### 25:00 - 28:00 Beat 4: write your own skill

**Screen:** a new file, `.claude/skills/hello-flow/SKILL.md`. Type, live:

```markdown
---
name: hello-flow
description: Create a connector-free test flow and run it. Use when asked for a smoke test, a hello world flow, or to check the setup works.
---

1. create_flow from demo-flow.json, with batch_size set to 1 so it succeeds.
2. run_flow, then list_runs until it finishes. Say Succeeded or explain_run it.
3. delete_flow with confirm=True. Report the flow name and how long it took.
```

New session, then: `Run a smoke test of my setup.` - it loads the skill on its own.

> "Three rules. The description is the trigger: write the words people actually say.
> The body is a procedure, not documentation. And write down what went wrong, not what
> the docs say."

### 28:00 - 30:00 Takeaways and install

1. Auth and transport are commodity. Shaping and docstrings are what you own.
2. Agents call **solution** flows. Build them as such, describe the tool well, publish.
3. Skills turn a procedure into something anyone on the team can ask for.

Repo: **github.com/OwnOptic/power-platform-mcp** - README, "The short version": ten
minutes to install. Then questions.

---

## Reset between rehearsals

In Claude Code, with `power-platform-extras` connected:
```
Delete every flow whose name starts with "DEMO - ", with confirm.
```
In Copilot Studio: Store Helper, Tools, delete "Get order status"; clear the
instructions; publish. Store Helper Next: delete it, or ask Claude to remove the order-status
skill and empty the instructions. The PAMCPDemo solution: delete it from make.powerapps.com >
Solutions if you want a clean environment (the table goes with it), or keep it - re-running
the schema step then reports everything as existing, which is also a fine demo.
Finally: `pac auth select` back to your everyday profile.

---

## Likely questions

**"Is it safe?"** It acts as you, so it can do anything you can: demo tenant, separate az
profile, `PA_TENANT_ID` pinned. Every tool is marked read-only or not, and `delete_flow`
asks twice.

**"Why not Microsoft's own Power Automate MCP?"** Use it when it covers you - it is
broader. Its run tools tell you where it broke, not why: they never follow the outputs
link. And your own server carries your own lessons in its docstrings.

**"Can it create the agent too?"** Not from here: an agent is created in Copilot Studio.
Everything after that - instructions, tools, skills, publish - it does, on both harnesses.

**"Which harness should we use?"** The standard one for a well-understood, rule-based
assistant; it is billed at publish. The GitHub Copilot harness for real multi-step work,
skills and files; it bills Copilot Credits from the first test, and an agent cannot move
from one harness to the other.

**"Can it create connections?"** No, and nothing honest can: a connection is an OAuth
sign-in. Portal once, then `bind_connection` forever.

**"Does it work with Copilot / Claude Desktop?"** Any MCP client. Claude Desktop takes the
same config in `claude_desktop_config.json`.
