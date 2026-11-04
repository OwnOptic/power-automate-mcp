---
name: dataverse-schema
description: Create Dataverse tables, columns, choices and lookups inside a new or existing solution, from a JSON spec, with scripts/dataverse_schema.py. Use when asked to create a table, add columns, design a data model, "make a schema", or set up a publisher and solution for a new project. Then hand over to solution-from-base to export it with pac.
---

# Create Dataverse schema in a solution

The script does the API work; your job is the spec, and reading the plan before you
apply it. Everything lands INSIDE the spec's solution, so `pac solution export` picks it
up afterwards.

## 1. Agree the target before anything else

- Which environment? `list_environments` (power-platform-extras) shows ids and which
  have Dataverse (`has_dataverse: true`). The script uses `PA_ENV_ID`, or `--env <id>`.
- Say the tenant and environment name back to the user and get a yes. The script acts as
  them and creates real metadata. Never a production environment for a demo.

## 2. Write the spec

Start from `schema/orders.example.json`. Keep the same publisher and solution for one
project; the prefix (3 to 8 lowercase letters) ends up on every schema name.

- Column types: `text`, `memo`, `integer`, `decimal`, `boolean`, `date`, `datetime`,
  `choice` (with `options`), `lookup` (with `target`, a table logical name such as
  `account` or `pmd_order`).
- Display names are what makers see; schema names are derived (`Due date` ->
  `pmd_DueDate`). Pick display names you will not want to rename: renaming is fine,
  the schema name never changes.
- A lookup is named after its target (`pmd_accountid`). Do not name a text column
  `<target>name` (`pmd_accountname`): a lookup reserves that name.
- One table per real-world thing. Status as a `choice`, never free text.

Save it as `schema/<project>.json`.

## 3. Plan, read, apply

```bash
python scripts/dataverse_schema.py plan  schema/<project>.json --env <id>
python scripts/dataverse_schema.py apply schema/<project>.json --env <id>
```

`plan` is read-only and lists what would be created. Show it to the user. `apply` takes
about 10 to 15 seconds per column (metadata calls are slow) and ends by publishing the
tables it touched. It is idempotent: re-running reports `exists` and changes nothing,
so to grow the model, add to the spec and apply again.

Run it with the repo's venv interpreter, and with `AZURE_CONFIG_DIR` set if the user
keeps a separate az profile for this tenant (the MCP config shows it).

## 4. Hand over

- To ship it or keep it in source control: the `solution-from-base` skill exports and
  unpacks it with pac.
- To use it from an agent: build an agent flow on it with `create_agent_flow` (the
  `agent-tool` skill).

## When it fails

- `0x80047013 attribute already exists`: a lookup reserved that name. Rename the column.
- `0x80040217 solution unique name is not valid` on the solution itself: a header leaked
  onto the solution POST. The script only sends it on metadata calls; do not add it.
- A `+` in a value used in a URL path returns an IIS HTML 404, not a Dataverse error.
- 401 or AADSTS errors: the az session for this tenant expired. `az login --tenant <id>`
  (with the same `AZURE_CONFIG_DIR`).
