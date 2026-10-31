"""Fixed-width files described by a record layout (the spreadsheet most partners send instead of a
copybook). Layout = CSV/YAML/list with: name, start (1-based) or offset (0-based), length,
optional record_type (multi-record files), type (string|integer|number|date), implied_decimals,
date_format.

    name,start,length,type,implied_decimals,date_format,record_type
    rec_type,1,2,string,,,
    claim_id,3,12,string,,,10
    paid_amt,15,9,number,2,,10
    svc_date,24,8,date,,%Y%m%d,10

Multi-record files (headers, details, trailers) are read with `record_type` and `type_field`;
each record type becomes its own table: `<dataset>` for the main type and `<dataset>.<type>` for
the others.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import yaml


@dataclass
class FieldDef:
    name: str
    offset: int
    length: int
    type: str = "string"
    implied_decimals: int = 0
    date_format: Optional[str] = None
    record_type: Optional[str] = None


def load_layout(layout: Union[str, Path, List[Dict[str, Any]]]) -> List[FieldDef]:
    if isinstance(layout, (str, Path)):
        path = Path(layout)
        if path.suffix.lower() in (".yaml", ".yml"):
            rows = yaml.safe_load(path.read_text(encoding="utf-8"))
            rows = rows.get("fields", rows) if isinstance(rows, dict) else rows
        else:
            rows = pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")
    else:
        rows = layout
    out = []
    for r in rows:
        r = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        if r.get("offset") not in (None, ""):
            offset = int(r["offset"])
        elif r.get("start") not in (None, ""):
            offset = int(r["start"]) - 1
        else:
            raise ValueError(f"layout row {r} needs start (1-based) or offset (0-based)")
        if offset < 0:
            raise ValueError(f"field {r.get('name')}: start must be >= 1")
        out.append(
            FieldDef(
                name=str(r["name"]),
                offset=offset,
                length=int(r["length"]),
                type=(r.get("type") or "string").lower(),
                implied_decimals=int(r.get("implied_decimals") or 0),
                date_format=r.get("date_format") or None,
                record_type=str(r["record_type"]) if r.get("record_type") not in (None, "") else None,
            )
        )
    return out


def _convert(value: str, f: FieldDef) -> Any:
    v = value.strip()
    if v == "":
        return None
    if f.type in ("integer", "number"):
        neg = v.endswith("-") or v.startswith("-")
        digits = v.strip("+-")
        if not digits.replace(".", "", 1).isdigit():
            return value  # left as text; a schema check reports it
        num = float(digits) / (10**f.implied_decimals) if f.implied_decimals else float(digits)
        num = -num if neg else num
        return int(num) if f.type == "integer" and num == int(num) else num
    if f.type == "date" and f.date_format:
        try:
            return pd.to_datetime(v, format=f.date_format).strftime("%Y-%m-%d")
        except ValueError:
            return value
    return value.rstrip() if f.type == "string" else v


def read_fixed_width(
    source: Union[str, Path, bytes],
    layout: Union[str, Path, List[Dict[str, Any]], List[FieldDef]],
    encoding: str = "utf-8",
    type_field: Optional[str] = None,
    main_type: Optional[str] = None,
    strict_length: bool = False,
) -> pd.DataFrame:
    """Return the main record type as a DataFrame (all records if the layout has no record types)."""
    fields: List[FieldDef]
    if isinstance(layout, list) and layout and all(isinstance(f, FieldDef) for f in layout):
        fields = [f for f in layout if isinstance(f, FieldDef)]
    else:
        fields = load_layout(layout)  # type: ignore[arg-type]
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    text = raw.decode(encoding)
    tables = _read_records(text, fields, type_field, strict_length)
    if main_type is None:
        main_type = next(iter(tables)) if tables else None
    empty = pd.DataFrame(columns=[f.name for f in fields])
    return tables.get(main_type, empty) if main_type is not None else empty


def read_fixed_width_tables(
    source: Union[str, Path, bytes],
    layout,
    encoding: str = "utf-8",
    type_field: Optional[str] = None,
    strict_length: bool = False,
) -> Dict[str, pd.DataFrame]:
    fields = load_layout(layout)
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    return _read_records(raw.decode(encoding), fields, type_field, strict_length)


def _read_records(
    text: str, fields: List[FieldDef], type_field: Optional[str], strict_length: bool
) -> Dict[str, pd.DataFrame]:
    typed = any(f.record_type for f in fields)
    tf = next((f for f in fields if f.name == type_field), None) if type_field else None
    if typed and tf is None:
        tf = next((f for f in fields if f.record_type is None), None)
    rows: Dict[str, List[Dict[str, Any]]] = {}
    width = max(f.offset + f.length for f in fields)
    for lineno, line in enumerate(io.StringIO(text), start=1):
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        if strict_length and len(line) != width and not typed:
            raise ValueError(f"line {lineno}: length {len(line)} != layout width {width}")
        rtype = line[tf.offset : tf.offset + tf.length].strip() if (typed and tf) else "_all"
        rec = {}
        for f in fields:
            if typed and f.record_type not in (None, rtype):
                continue
            rec[f.name] = _convert(line[f.offset : f.offset + f.length], f)
        rows.setdefault(rtype, []).append(rec)
    return {k: pd.DataFrame(v) for k, v in rows.items()}
