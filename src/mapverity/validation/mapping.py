"""Turn a source-to-target mapping document (STTM / data-model mapping) into executable checks.

The mapping document is the CSV or Excel sheet data teams already maintain. One row per target
field:

    target_table, target_field, source_dataset, source_field, rule, data_type, required, key,
    allowed_values, source_filter, description

`rule` is how the target value is derived from the source record:

    (blank) | direct          copy source_field as-is
    trim | upper | lower | title | upper_trim
    lookup: M=MALE, F=FEMALE, *=UNKNOWN     code translation; * is the default
    date: %Y%m%d               reformat a source date to ISO YYYY-MM-DD
    concat: FIRST-NAME, LAST-NAME            joined with a single space
    constant: LEGACY           a literal
    count                      array size == number of matching source records (use on <array>._count)
    expr: <expression>         any mapverity expression over source columns
    none                       documented but not validated (shows up as a coverage gap)

Rows with key = Y define how target records join back to source records (several key rows
form a composite key). `source_filter` on a key row tells mapverity which source records the
pipeline is expected to load.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..core.spec import CheckSpec, DatasetSpec, Spec

_ALIASES = {
    "target_table": ["target_table", "target_collection", "collection", "target_entity", "target"],
    "target_field": ["target_field", "target_column", "target_path", "target_attribute", "field_path"],
    "source_dataset": ["source_dataset", "source_file", "source_table", "source"],
    "source_field": ["source_field", "source_column", "source_attribute"],
    "rule": ["rule", "transformation", "transformation_rule", "logic", "mapping_rule"],
    "data_type": ["data_type", "target_type", "type", "datatype"],
    "required": ["required", "mandatory", "nullable_n", "not_null"],
    "key": ["key", "is_key", "join_key"],
    "allowed_values": ["allowed_values", "valid_values", "domain"],
    "source_filter": ["source_filter", "filter", "selection_criteria"],
    "tolerance": ["tolerance", "numeric_tolerance", "tolerance_abs"],
    "pattern": ["pattern", "format_regex", "regex"],
    "references": ["references", "foreign_key", "reference", "lookup_table"],
    "description": ["description", "comments", "notes"],
}
_TYPES = {
    "string": "string",
    "str": "string",
    "varchar": "string",
    "char": "string",
    "text": "string",
    "int": "integer",
    "integer": "integer",
    "long": "integer",
    "number": "number",
    "decimal": "number",
    "double": "number",
    "float": "number",
    "numeric": "number",
    "date": "date",
    "datetime": "datetime",
    "timestamp": "datetime",
    "bool": "boolean",
    "boolean": "boolean",
}


@dataclass
class FieldMapping:
    target_table: str
    target_field: str
    source_dataset: Optional[str] = None
    source_field: Optional[str] = None
    rule: str = ""
    data_type: Optional[str] = None
    required: bool = False
    key: bool = False
    allowed_values: List[str] = field(default_factory=list)
    source_filter: Optional[str] = None
    description: Optional[str] = None
    tolerance: Optional[float] = None  # absolute numeric tolerance (rounding-mode differences between engines)
    pattern: Optional[str] = None  # full-match regex the target value must satisfy
    references: Optional[str] = None  # dataset.column the value must exist in (foreign key)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("y", "yes", "true", "1", "x")


def _clean(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def load_mapping(path: Union[str, Path], sheet: Union[str, int] = 0) -> List[FieldMapping]:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    norm = {c: re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in df.columns}
    df = df.rename(columns=norm)
    colmap = {}
    for canon, names in _ALIASES.items():
        for n in names:
            if n in df.columns:
                colmap[canon] = n
                break
    for required in ("target_table", "target_field"):
        if required not in colmap:
            raise ValueError(f"mapping document needs a '{required}' column (have {list(df.columns)})")
    out = []
    for _, r in df.iterrows():

        def get(k, r=r):
            return _clean(r[colmap[k]]) if k in colmap else None

        if not get("target_field"):
            continue
        dt = get("data_type")
        out.append(
            FieldMapping(
                target_table=get("target_table"),
                target_field=get("target_field"),
                source_dataset=get("source_dataset"),
                source_field=get("source_field"),
                rule=get("rule") or "",
                data_type=_TYPES.get(dt.lower(), dt.lower()) if dt else None,
                required=_truthy(get("required") or ""),
                key=_truthy(get("key") or ""),
                allowed_values=[v.strip() for v in re.split(r"[|;]", get("allowed_values") or "") if v.strip()],
                source_filter=get("source_filter"),
                description=get("description"),
                tolerance=float(get("tolerance")) if get("tolerance") else None,
                pattern=get("pattern"),
                references=get("references"),
            )
        )
    return out


def _src(m: FieldMapping) -> str:
    if not m.source_field:
        raise ValueError(f"{m.target_table}.{m.target_field}: rule '{m.rule}' needs a source_field")
    return f'col("{m.source_field}")'


def rule_to_expression(m: FieldMapping) -> Optional[str]:
    """Translate a mapping rule into a mapverity expression (None = not validated)."""
    rule = m.rule.strip()
    kind, _, arg = rule.partition(":")
    kind, arg = kind.strip().lower(), arg.strip()
    if kind in ("", "direct", "move", "copy", "as-is", "as is"):
        return _src(m) if m.source_field else None
    if kind == "none":
        return None
    simple = {
        "trim": "strip({})",
        "upper": "upper({})",
        "lower": "lower({})",
        "title": "title({})",
        "upper_trim": "upper(strip({}))",
        "trim_upper": "upper(strip({}))",
    }
    if kind in simple:
        return simple[kind].format(_src(m))
    if kind == "lookup":
        table, default = {}, None
        for pair in arg.split(","):
            if "=" not in pair:
                continue
            k, v = (p.strip() for p in pair.split("=", 1))
            if k == "*":
                default = v
            else:
                table[k] = v
        return f"lookup({_src(m)}, {json.dumps(table)}, {default!r})"
    if kind == "date":
        return f"to_date({_src(m)}, {json.dumps(arg)})" if arg else f"to_date({_src(m)})"
    if kind == "concat":
        parts = [p.strip() for p in arg.split(",") if p.strip()]
        return "concat(" + ", ".join(f'col("{p}")' for p in parts) + ")"
    if kind == "constant":
        return json.dumps(arg)
    if kind == "expr":
        return arg
    if kind == "count":
        return "1"
    if kind == "ref":
        return None  # compiled to a referential check, not a value derivation
    raise ValueError(f"{m.target_table}.{m.target_field}: unknown rule '{m.rule}'")


@dataclass
class TableMapping:
    name: str
    fields: List[FieldMapping]

    @property
    def keys(self) -> List[FieldMapping]:
        return [f for f in self.fields if f.key]

    @property
    def source(self) -> Optional[str]:
        for f in self.keys + self.fields:
            if f.source_dataset:
                return f.source_dataset
        return None

    @property
    def source_filter(self) -> Optional[str]:
        for f in self.keys:
            if f.source_filter:
                return f.source_filter
        return None

    def key_params(self) -> Dict[str, Any]:
        if not self.keys:
            raise ValueError(f"table '{self.name}' has no key rows (key = Y)")
        tk = [k.target_field for k in self.keys]
        sk = []
        for k in self.keys:
            expr = rule_to_expression(k)
            sk.append(k.source_field if expr == f'col("{k.source_field}")' else expr)
        if len(tk) == 1:
            return {"source_key": sk[0], "target_key": tk[0]}
        return {"source_key": sk, "target_key": tk}


def group_tables(mappings: List[FieldMapping]) -> List[TableMapping]:
    order: Dict[str, List[FieldMapping]] = {}
    for m in mappings:
        order.setdefault(m.target_table, []).append(m)
    return [TableMapping(t, f) for t, f in order.items()]


def _parent_link(table: "TableMapping", tables: Dict[str, "TableMapping"]) -> Optional[Dict[str, Any]]:
    """For an array table (`members.eligibility`), find which of its key fields is the parent's key."""
    if "." not in table.name:
        return None
    parent = tables.get(table.name.rsplit(".", 1)[0])
    if parent is None or not parent.keys:
        return None
    parent_key = parent.keys[0].target_field
    for k in table.keys:
        if k.target_field == parent_key:
            expr = rule_to_expression(k)
            source_key = k.source_field if expr == f'col("{k.source_field}")' else expr
            return {"dataset": parent.name, "key": parent_key, "source_key": source_key}
    return None


def spec_from_mapping(
    mappings: Union[List[FieldMapping], str, Path],
    name: str = "data-model-mapping",
    datasets: Optional[Dict[str, DatasetSpec]] = None,
) -> Spec:
    """Generate a spec that validates every mapped target field against its source rule."""
    if not isinstance(mappings, list):
        mappings = load_mapping(mappings)
    checks: List[CheckSpec] = []
    tables_by_name = {tm.name: tm for tm in group_tables(mappings)}
    for table in group_tables(mappings):
        t = table.name
        types = {f.target_field: f.data_type for f in table.fields if f.data_type}
        if types:
            checks.append(CheckSpec("schema", dataset=t, name=f"model:{t}:types", params={"columns": types}))
        for f in table.fields:
            if f.required:
                checks.append(
                    CheckSpec("not_null", dataset=t, column=f.target_field, name=f"model:{t}:{f.target_field}:required")
                )
            if f.allowed_values:
                checks.append(
                    CheckSpec(
                        "allowed_values",
                        dataset=t,
                        column=f.target_field,
                        name=f"model:{t}:{f.target_field}:domain",
                        params={"values": f.allowed_values},
                    )
                )
            if f.pattern:
                checks.append(
                    CheckSpec(
                        "regex",
                        dataset=t,
                        column=f.target_field,
                        name=f"model:{t}:{f.target_field}:pattern",
                        params={"pattern": f.pattern},
                    )
                )
            ref = f.references or (
                f.rule.split(":", 1)[1].strip() if f.rule.strip().lower().startswith("ref:") else None
            )
            if ref:
                ref_ds, _, ref_col = ref.rpartition(".")
                if not ref_ds or not ref_col:
                    raise ValueError(f"{t}.{f.target_field}: rule 'ref:' needs dataset.column, got '{ref}'")
                checks.append(
                    CheckSpec(
                        "referential",
                        dataset=t,
                        column=f.target_field,
                        name=f"ref:{t}:{f.target_field}",
                        params={"ref_dataset": ref_ds, "ref_column": ref_col},
                    )
                )
        if table.keys:
            checks.append(
                CheckSpec(
                    "unique",
                    dataset=t,
                    name=f"model:{t}:key-unique",
                    params={"columns": [k.target_field for k in table.keys]},
                )
            )
        src = table.source
        if not src or not table.keys:
            continue
        kp = table.key_params()
        sf = table.source_filter
        km = {"source": src, "target": t, "mode": "equal", **kp}
        parent = _parent_link(table, tables_by_name)
        if parent:
            km["parent"] = parent  # array table: compare against source records of loaded parents only
        elif "." in t:
            km["mode"] = "subset"  # parent key not mapped: can only check that no element is invented
        if sf:
            km["source_filter"] = sf
        checks.append(CheckSpec("key_match", dataset=t, name=f"recon:{t}:keys", params=km))
        for f in table.fields:
            if f.key:
                continue
            fsrc = f.source_dataset or src
            expr = rule_to_expression(f)
            if expr is None:
                continue
            if f.rule.strip().lower() == "count":
                # array size == number of child records in fsrc for this parent
                ck = f.source_field or (kp["source_key"] if isinstance(kp["source_key"], str) else kp["source_key"][0])
                tk = kp["target_key"] if isinstance(kp["target_key"], str) else kp["target_key"][0]
                params = {"source": fsrc, "source_key": ck, "target_key": tk, "expression": "1", "agg": "count"}
                if f.source_filter:
                    params["source_filter"] = f.source_filter
                checks.append(
                    CheckSpec(
                        "group_aggregate",
                        dataset=t,
                        column=f.target_field,
                        name=f"map:{t}:{f.target_field}",
                        params=params,
                    )
                )
                continue
            params = {"source": fsrc, **kp, "expression": expr}
            if sf and fsrc == src:
                params["source_filter"] = sf
            if f.tolerance is not None:
                params["tolerance"] = f.tolerance
            elif f.data_type in ("number",):
                params["tolerance"] = 0.005
            checks.append(
                CheckSpec("mapping", dataset=t, column=f.target_field, name=f"map:{t}:{f.target_field}", params=params)
            )
    return Spec(name=name, datasets=dict(datasets or {}), checks=checks)


@dataclass
class Coverage:
    mapped: List[str]
    value_validated: List[str]  # value re-derived from source and compared
    constraint_only: List[str]  # only type / required / domain checks (value not derivable)
    not_validated: List[str]  # documented, nothing executable
    unmapped_in_data: List[str]  # present in target data, absent from the mapping document
    missing_in_data: List[str]  # in the mapping document, never present in target data

    @property
    def ratio(self) -> float:
        return len(self.value_validated) / len(self.mapped) if self.mapped else 0.0

    def to_dict(self):
        return {
            "fields_in_mapping": len(self.mapped),
            "value_validated": len(self.value_validated),
            "constraint_only": self.constraint_only,
            "not_validated": self.not_validated,
            "value_coverage": round(self.ratio, 4),
            "unmapped_in_data": self.unmapped_in_data,
            "missing_in_data": self.missing_in_data,
        }


def mapping_coverage(mappings: List[FieldMapping], data: Dict[str, pd.DataFrame]) -> Coverage:
    mapped, value, constraint, nothing, missing = [], [], [], [], []
    by_table: Dict[str, set] = {}
    for m in mappings:
        fq = f"{m.target_table}.{m.target_field}"
        mapped.append(fq)
        by_table.setdefault(m.target_table, set()).add(m.target_field)
        derivable = (
            (m.source_dataset is not None or m.rule.lower().startswith("constant"))
            and rule_to_expression(m) is not None
        ) or m.rule.lower().startswith("ref:")
        if m.key or derivable:
            value.append(fq)
        elif m.required or m.allowed_values or m.data_type:
            constraint.append(fq)
        else:
            nothing.append(fq)
        df = data.get(m.target_table)
        if df is not None and m.target_field not in df.columns:
            missing.append(fq)
    unmapped = []
    for table, fields in by_table.items():
        df = data.get(table)
        if df is None:
            continue
        for c in df.columns:
            if c not in fields and not c.startswith("_index"):
                unmapped.append(f"{table}.{c}")
    return Coverage(mapped, value, constraint, nothing, sorted(unmapped), missing)
