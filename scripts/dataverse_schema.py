"""Create Dataverse schema from a JSON spec: publisher, solution, tables, columns, lookups.

    python scripts/dataverse_schema.py apply  schema/orders.json
    python scripts/dataverse_schema.py plan   schema/orders.json     (read-only: what would change)

Used by the `dataverse-schema` skill. Same auth as the MCP servers - the Azure CLI
session, a token for the environment's org URL - and the same target: PA_ENV_ID, or
the tenant default. Pass --env <environment id> to aim elsewhere.

Idempotent: anything that already exists is reported and left alone, so you can grow
a spec and re-run it. Everything is created INSIDE the spec's solution, which is what
`pac solution export` then picks up.

The spec (see schema/orders.example.json):

    {
      "publisher": {"uniquename": "pamcpdemo", "friendlyname": "PA MCP Demo",
                    "prefix": "pmd", "optionvalueprefix": 71000},
      "solution":  {"uniquename": "PAMCPDemo", "friendlyname": "PA MCP Demo"},
      "language":  1033,
      "tables": [
        {"name": "Order", "plural": "Orders", "description": "...",
         "primary": "Order number",
         "columns": [
           {"name": "Status",   "type": "choice", "options": ["New", "Shipped", "Delivered"]},
           {"name": "Amount",   "type": "decimal"},
           {"name": "Due date", "type": "date"},
           {"name": "Notes",    "type": "memo"},
           {"name": "Priority", "type": "boolean"},
           {"name": "Quantity", "type": "integer"},
           {"name": "Customer", "type": "lookup", "target": "account"}
         ]}
      ]
    }

Schema names are derived: prefix + display name without spaces ("pmd_DueDate"). Types:
text, memo, integer, decimal, boolean, date, datetime, choice, lookup.

Gotchas this encodes (each one cost time on a real build):
- a lookup named `x` reserves `xname`, so a lookup is named after its TARGET plus "id"
  (pmd_accountid), which never collides with a text column;
- MSCRM.SolutionUniqueName on the publisher or solution POST fails (the solution does
  not exist yet), so it is sent only on metadata calls;
- metadata created through the API is not visible in forms and views until it is
  published, so the script ends with PublishXml for the tables it touched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from extras import _dataverse, _dataverse_headers, _org_url  # noqa: E402

TYPES = {"text", "memo", "integer", "decimal", "boolean", "date", "datetime", "choice", "lookup"}


def label(text: str, lang: int) -> dict:
    return {"LocalizedLabels": [{"Label": text, "LanguageCode": lang}]}


def schema_name(prefix: str, display: str) -> str:
    return f"{prefix}_" + re.sub(r"[^A-Za-z0-9]", "", display.title())


class Dataverse:
    def __init__(self, env: str, solution: str | None = None, dry_run: bool = False) -> None:
        self.base = f"{_org_url(env)}/api/data/v9.2"
        self.headers = _dataverse_headers(env)
        self.solution = solution
        self.dry_run = dry_run

    def get(self, path: str, **params: str) -> dict | None:
        try:
            return _dataverse("GET", f"{self.base}/{path}", headers=self.headers, params=params or None).json()
        except server.PowerAutomateError as exc:
            if "404" in str(exc) or "Does Not Exist" in str(exc) or "Could not find" in str(exc):
                return None
            raise

    def write(self, method: str, path: str, body: dict, in_solution: bool = True) -> None:
        if self.dry_run:
            return
        headers = dict(self.headers)
        if in_solution and self.solution:
            headers["MSCRM.SolutionUniqueName"] = self.solution
        _dataverse(method, f"{self.base}/{path}", headers=headers, json=body, timeout=180)


def column_body(col: dict, prefix: str, lang: int, optionvalueprefix: int) -> dict:
    kind = col["type"]
    name = schema_name(prefix, col["name"])
    common = {"SchemaName": name, "DisplayName": label(col["name"], lang),
              "Description": label(col.get("description", col["name"]), lang),
              "RequiredLevel": {"Value": "ApplicationRequired" if col.get("required") else "None"}}
    if kind == "text":
        return {"@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata", **common,
                "MaxLength": col.get("max_length", 200), "FormatName": {"Value": "Text"}}
    if kind == "memo":
        return {"@odata.type": "Microsoft.Dynamics.CRM.MemoAttributeMetadata", **common,
                "MaxLength": col.get("max_length", 4000), "Format": "TextArea"}
    if kind == "integer":
        return {"@odata.type": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata", **common,
                "MinValue": -2147483648, "MaxValue": 2147483647, "Format": "None"}
    if kind == "decimal":
        return {"@odata.type": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata", **common,
                "Precision": col.get("precision", 2), "MinValue": -100000000000, "MaxValue": 100000000000}
    if kind == "boolean":
        return {"@odata.type": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata", **common,
                "DefaultValue": False, "OptionSet": {
                    "@odata.type": "Microsoft.Dynamics.CRM.BooleanOptionSetMetadata",
                    "TrueOption": {"Value": 1, "Label": label("Yes", lang)},
                    "FalseOption": {"Value": 0, "Label": label("No", lang)}}}
    if kind in ("date", "datetime"):
        return {"@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata", **common,
                "Format": "DateOnly" if kind == "date" else "DateAndTime",
                "DateTimeBehavior": {"Value": "DateOnly" if kind == "date" else "UserLocal"}}
    if kind == "choice":
        return {"@odata.type": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata", **common, "OptionSet": {
            "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata", "IsGlobal": False, "OptionSetType": "Picklist",
            "Options": [{"Value": optionvalueprefix * 10000 + i, "Label": label(o, lang)}
                        for i, o in enumerate(col["options"])]}}
    raise ValueError(f"unknown column type {kind!r}; use one of {sorted(TYPES)}")


def apply(spec: dict, env: str, dry_run: bool) -> list[str]:
    pub, sol, lang = spec["publisher"], spec["solution"], spec.get("language", 1033)
    prefix, ovp = pub["prefix"], pub.get("optionvalueprefix", 10000)
    dv = Dataverse(env, sol["uniquename"], dry_run)
    log: list[str] = []

    found = dv.get("publishers", **{"$select": "publisherid", "$filter": f"uniquename eq '{pub['uniquename']}'"})
    if found and found["value"]:
        publisher_id = found["value"][0]["publisherid"]
        log.append(f"publisher {pub['uniquename']}: exists")
    else:
        dv.write("POST", "publishers", {
            "uniquename": pub["uniquename"], "friendlyname": pub["friendlyname"],
            "customizationprefix": prefix, "customizationoptionvalueprefix": ovp}, in_solution=False)
        log.append(f"publisher {pub['uniquename']}: created")
        found = dv.get("publishers", **{"$select": "publisherid", "$filter": f"uniquename eq '{pub['uniquename']}'"})
        publisher_id = found["value"][0]["publisherid"] if found and found["value"] else "<new>"

    found = dv.get("solutions", **{"$select": "solutionid", "$filter": f"uniquename eq '{sol['uniquename']}'"})
    if found and found["value"]:
        log.append(f"solution {sol['uniquename']}: exists")
    else:
        dv.write("POST", "solutions", {
            "uniquename": sol["uniquename"], "friendlyname": sol["friendlyname"], "version": sol.get("version", "1.0.0.0"),
            "publisherid@odata.bind": f"/publishers({publisher_id})"}, in_solution=False)
        log.append(f"solution {sol['uniquename']}: created")

    touched: list[str] = []
    for table in spec.get("tables", []):
        t_schema = schema_name(prefix, table["name"])
        t_logical = t_schema.lower()
        exists = dv.get(f"EntityDefinitions(LogicalName='{t_logical}')", **{"$select": "LogicalName"})
        if exists:
            log.append(f"table {t_logical}: exists")
        else:
            primary = table.get("primary", "Name")
            dv.write("POST", "EntityDefinitions", {
                "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
                "SchemaName": t_schema, "DisplayName": label(table["name"], lang),
                "DisplayCollectionName": label(table.get("plural", table["name"] + "s"), lang),
                "Description": label(table.get("description", table["name"]), lang),
                "OwnershipType": "UserOwned", "HasActivities": False, "HasNotes": False, "IsActivity": False,
                "Attributes": [{
                    "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
                    "SchemaName": schema_name(prefix, primary), "IsPrimaryName": True,
                    "RequiredLevel": {"Value": "ApplicationRequired"}, "MaxLength": 100,
                    "FormatName": {"Value": "Text"}, "DisplayName": label(primary, lang),
                    "Description": label(primary, lang)}],
            })
            log.append(f"table {t_logical}: created")
        touched.append(t_logical)

        for col in table.get("columns", []):
            if col["type"] not in TYPES:
                raise SystemExit(f"{table['name']}.{col['name']}: unknown type {col['type']!r}; use one of {sorted(TYPES)}")
            if col["type"] == "lookup":
                target = col["target"].lower()
                lookup = f"{prefix}_{target.split('_')[-1]}id"  # name after the TARGET: never collides
                if dv.get(f"EntityDefinitions(LogicalName='{t_logical}')/Attributes(LogicalName='{lookup}')",
                          **{"$select": "LogicalName"}):
                    log.append(f"  lookup {lookup}: exists")
                    continue
                dv.write("POST", "RelationshipDefinitions", {
                    "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                    "SchemaName": f"{prefix}_{target.split('_')[-1]}_{t_schema.split('_', 1)[1]}",
                    "ReferencedEntity": target, "ReferencingEntity": t_logical,
                    "CascadeConfiguration": {"Assign": "NoCascade", "Delete": "RemoveLink", "Merge": "NoCascade",
                                             "Reparent": "NoCascade", "Share": "NoCascade", "Unshare": "NoCascade"},
                    "Lookup": {"@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
                               "SchemaName": lookup,
                               "DisplayName": label(col["name"], lang),
                               "Description": label(col.get("description", col["name"]), lang),
                               "RequiredLevel": {"Value": "None"}},
                })
                log.append(f"  lookup {lookup} -> {target}: created")
                continue
            logical = schema_name(prefix, col["name"]).lower()
            if dv.get(f"EntityDefinitions(LogicalName='{t_logical}')/Attributes(LogicalName='{logical}')",
                      **{"$select": "LogicalName"}):
                log.append(f"  column {logical}: exists")
                continue
            dv.write("POST", f"EntityDefinitions(LogicalName='{t_logical}')/Attributes",
                     column_body(col, prefix, lang, ovp))
            log.append(f"  column {logical} ({col['type']}): created")

    if touched and not dry_run:
        xml = "".join(f"<entity>{t}</entity>" for t in touched)
        dv.write("POST", "PublishXml", {"ParameterXml": f"<importexportxml><entities>{xml}</entities></importexportxml>"},
                 in_solution=False)
        log.append(f"published: {', '.join(touched)}")
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["apply", "plan"])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--env", default="", help="environment id; default PA_ENV_ID or the tenant default")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    env = args.env or server.env_id()
    print(f"environment {env}  org {_org_url(env)}  mode {args.mode}")
    for line in apply(spec, env, dry_run=args.mode == "plan"):
        print(("would: " if args.mode == "plan" and "created" in line else "") + line)


if __name__ == "__main__":
    main()
