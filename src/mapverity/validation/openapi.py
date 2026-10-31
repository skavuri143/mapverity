"""Compile an OpenAPI 3 / JSON Schema object schema into field-level checks, so API responses (read
with the `rest` connector) are validated against their published contract.

    from mapverity.validation.openapi import spec_from_openapi
    spec = spec_from_openapi("openapi.yaml", schema="Member", dataset="members_api")

Supported: required, type/format (string, integer, number, boolean, date, date-time), enum,
pattern, minimum/maximum, minLength/maxLength (as a length rule), nested objects (dotted
columns) and arrays of objects (child tables, as produced by the document flattener).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from ..core.spec import CheckSpec, Spec

_TYPES = {"integer": "integer", "number": "number", "boolean": "boolean", "string": "string"}


def _load(doc: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(doc, dict):
        return doc
    text = Path(doc).read_text(encoding="utf-8")
    return json.loads(text) if str(doc).endswith(".json") else yaml.safe_load(text)


def _resolve(node: Dict[str, Any], root: Dict[str, Any]) -> Dict[str, Any]:
    seen = 0
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported, got {ref}")
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        seen += 1
        if seen > 32:
            raise ValueError("circular $ref")
    if "allOf" in node:
        merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for sub in node["allOf"]:
            sub = _resolve(sub, root)
            merged["properties"].update(sub.get("properties", {}))
            merged["required"] += sub.get("required", [])
        return merged
    return node


def _walk(
    schema: Dict[str, Any], root: Dict[str, Any], table: str, prefix: str, required_ctx: bool, checks: List[CheckSpec]
) -> None:
    schema = _resolve(schema, root)
    required = set(schema.get("required", []))
    types: Dict[str, str] = {}
    for name, prop in (schema.get("properties") or {}).items():
        prop = _resolve(prop, root)
        col = f"{prefix}{name}"
        is_req = name in required and required_ctx
        ptype = prop.get("type")
        if ptype == "object" or "properties" in prop:
            _walk(prop, root, table, col + ".", is_req, checks)
            continue
        if ptype == "array":
            items = _resolve(prop.get("items", {}), root)
            child = f"{table}.{col}"
            if items.get("type") == "object" or "properties" in items:
                _walk(items, root, child, "", True, checks)
            if prop.get("minItems") is not None:
                checks.append(
                    CheckSpec(
                        "in_range",
                        dataset=table,
                        column=f"{col}._count",
                        name=f"api:{table}:{col}:minItems",
                        params={"min": prop["minItems"]},
                    )
                )
            continue
        fmt = prop.get("format")
        if ptype == "string" and fmt in ("date", "date-time"):
            types[col] = "date" if fmt == "date" else "datetime"
        elif ptype in _TYPES:
            types[col] = _TYPES[ptype]
        if is_req and not prop.get("nullable", False):
            checks.append(CheckSpec("not_null", dataset=table, column=col, name=f"api:{table}:{col}:required"))
        if prop.get("enum"):
            checks.append(
                CheckSpec(
                    "allowed_values",
                    dataset=table,
                    column=col,
                    name=f"api:{table}:{col}:enum",
                    params={"values": [v for v in prop["enum"] if v is not None]},
                )
            )
        if prop.get("pattern"):
            pat = prop["pattern"].lstrip("^").rstrip("$")
            checks.append(
                CheckSpec(
                    "regex", dataset=table, column=col, name=f"api:{table}:{col}:pattern", params={"pattern": pat}
                )
            )
        if prop.get("minimum") is not None or prop.get("maximum") is not None:
            params = {k: prop[k2] for k, k2 in (("min", "minimum"), ("max", "maximum")) if prop.get(k2) is not None}
            checks.append(
                CheckSpec("in_range", dataset=table, column=col, name=f"api:{table}:{col}:range", params=params)
            )
        lo, hi = prop.get("minLength"), prop.get("maxLength")
        if lo is not None or hi is not None:
            conds = []
            if lo is not None:
                conds.append(f'(length(col("{col}")) >= {int(lo)})')
            if hi is not None:
                conds.append(f'(length(col("{col}")) <= {int(hi)})')
            checks.append(
                CheckSpec(
                    "row_rule",
                    dataset=table,
                    name=f"api:{table}:{col}:length",
                    params={"expression": " & ".join(conds)},
                )
            )
    if types:
        checks.insert(
            0,
            CheckSpec(
                "schema",
                dataset=table,
                name=f"api:{table}:types{':' + prefix.rstrip('.') if prefix else ''}",
                params={"columns": types},
            ),
        )


def spec_from_openapi(
    document: Union[str, Path, Dict[str, Any]], schema: str, dataset: str, name: Optional[str] = None
) -> Spec:
    """Checks for one schema in `components.schemas` (or `definitions` in Swagger 2)."""
    root = _load(document)
    schemas = (root.get("components") or {}).get("schemas") or root.get("definitions") or {}
    if schema not in schemas:
        raise KeyError(f"schema '{schema}' not found; have {sorted(schemas)}")
    checks: List[CheckSpec] = []
    _walk(schemas[schema], root, dataset, "", True, checks)
    return Spec(name=name or f"openapi-{schema}", checks=checks)
