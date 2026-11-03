---
name: solution-from-base
description: Take an existing Power Platform solution - exported from an environment with pac, or a .zip the user drops in - unpack it, change it as a base (add a column, a table, a flow, rename, retarget), pack it and import it back with the Power Platform CLI. Use when asked to export, import, copy, clone, extend or modify a solution, or "start from this solution".
---

# Modify a solution as a base, with pac

A solution is a zip of XML. Unpacked, it is text Claude can read and edit like code:
the safest way to add something is to copy a block that already works and change it.

## 0. Point pac at the right place - every time

pac keeps several sign-ins and uses the ACTIVE one. On a consultant's machine that can
be a client's production environment.

```bash
pac auth list                                   # the * row is what bare commands hit
pac auth select --name <profile for this tenant>
```

Pass `--environment <org url>` on every export and import anyway, say the environment
back to the user, and select their previous profile again when you are done. No
profile for this tenant yet: `pac auth create --name <name> --environment <org url>`
(the user signs in in the browser).

## 1. Get the base

From an environment:

```bash
pac solution list --environment <org url>
pac solution export --name <UniqueName> --path base.zip --managed false --environment <org url>
```

Or the user gives you a .zip: use it as `base.zip`. Unmanaged only - a managed solution
is a sealed copy, and its components cannot be changed where it is installed.

```bash
pac solution unpack --zipfile base.zip --folder src --packagetype Unmanaged
```

## 2. Read it before changing it

Summarise for the user what is inside: `src/Other/Solution.xml` (name, publisher,
version), `src/Entities/*/Entity.xml` (tables and columns), `src/Workflows/` (flows),
`src/bots/` and `src/botcomponents/` (Copilot Studio agents). Note the publisher prefix:
everything you add uses it.

## 3. Change it - copy a working block

- **A column**: in `src/Entities/<Table>/Entity.xml`, copy an existing `<attribute>` of
  the same type (text from text, choice from choice), then change `PhysicalName`,
  `Name`, `LogicalName` (prefix_lowercase), display name, description, length.
- **A table, several tables, choices with labels**: generating the XML by hand is
  error-prone. Create them in the environment with the `dataverse-schema` skill instead,
  then export again.
- **Agent instructions**: `src/botcomponents/<schema>.gpt.default/data`, the
  `instructions: |-` block. No `{` or `}` in it: Copilot Studio reads them as Power Fx.
- **A flow**: `src/Workflows/*.json` is the definition, same JSON as `get_flow`.

Then ALWAYS bump `<Version>` in `src/Other/Solution.xml` (1.0.0.0 -> 1.1.0.0). An import
of the same version can report success and change nothing.

## 4. Pack, check, import

```bash
pac solution pack --zipfile v1.1.zip --folder src --packagetype Unmanaged
pac solution check --path v1.1.zip                      # optional static analysis
pac solution import --path v1.1.zip --environment <org url> --publish-changes
```

Import takes one to three minutes. Then verify the change is really there - ask
Dataverse for the new column, `get_agent` for an agent, `get_flow` for a flow - rather
than trusting "Solution Imported successfully".

## Things that look like errors and are not

- `0x80045042 The original workflow definition has been deactivated and replaced`: the
  normal message when an import updates a flow. Check the flow is On afterwards.
- Importing a solution with Copilot Studio agents publishes them by itself, about 15
  seconds after the import finishes.

## Things that are errors

- `Bot name length greater than 42 characters` (only visible in the import job): agent
  display names must stay at 42 characters or fewer.
- Missing dependency: the solution uses a component (a table, a connection reference)
  that is not in the target. Import its solution first, or add the component.
