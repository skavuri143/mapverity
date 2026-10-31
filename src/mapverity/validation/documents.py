"""Flatten JSON documents (e.g. a DocumentDB / MongoDB collection) into testable tables.

A document collection becomes one root table plus one child table per array path:

    {"member_id": "M1", "name": {"first": "Ana"}, "eligibility": [{"aid": "A1"}, {"aid": "B2"}]}

    members               -> member_id | name.first | eligibility._count
    members.eligibility   -> member_id | _index | aid

Child tables carry their ancestors' keys so they can be joined back to source records with a
composite key, and every array contributes a `<path>._count` column on its parent so that
"one array element per source segment" can be checked with a group aggregate.
"""

from __future__ import annotations

import datetime as _dt
import decimal
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


def _scalar(v: Any) -> Any:
    """Normalise BSON / extended-JSON scalars."""
    if isinstance(v, dict) and len(v) == 1:
        ((k, inner),) = v.items()
        if k == "$oid":
            return str(inner)
        if k == "$date":
            inner = inner.get("$numberLong", inner) if isinstance(inner, dict) else inner
            if isinstance(inner, (int, float)) or (isinstance(inner, str) and inner.lstrip("-").isdigit()):
                return _dt.datetime.fromtimestamp(int(inner) / 1000, _dt.UTC).isoformat()
            return str(inner)
        if k in ("$numberDecimal", "$numberDouble"):
            return float(inner)
        if k in ("$numberLong", "$numberInt"):
            return int(inner)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    type_name = type(v).__name__
    if type_name == "ObjectId":
        return str(v)
    if type_name == "Decimal128":
        return float(v.to_decimal())
    return v


def _is_ext_json(v: Any) -> bool:
    return isinstance(v, dict) and len(v) == 1 and next(iter(v)).startswith("$")


def flatten_documents(
    docs: Iterable[Dict[str, Any]], name: str = "documents", key: Optional[str] = None, sep: str = "."
) -> Dict[str, pd.DataFrame]:
    """Return {table_name: DataFrame}. `key` is the document key (defaults to `_id`)."""
    tables: Dict[str, List[Dict[str, Any]]] = {name: []}

    def walk(obj: Dict[str, Any], prefix: str, row: Dict[str, Any], table: str, parent_keys: Dict[str, Any]):
        for k, v in obj.items():
            path = f"{prefix}{k}"
            if isinstance(v, dict) and not _is_ext_json(v):
                walk(v, path + sep, row, table, parent_keys)
            elif isinstance(v, list):
                row[f"{path}{sep}_count"] = len(v)
                child = f"{table}{sep}{path}"
                tables.setdefault(child, [])
                for i, el in enumerate(v):
                    crow: Dict[str, Any] = dict(parent_keys)
                    idx_col = "_index" if table == name else f"_index{sep}{path}"
                    crow[idx_col] = i
                    ckeys = dict(parent_keys)
                    ckeys[idx_col] = i
                    if isinstance(el, dict) and not _is_ext_json(el):
                        walk(el, "", crow, child, ckeys)
                    else:
                        crow["value"] = _scalar(el)
                    tables[child].append(crow)
            else:
                row[path] = _scalar(v)

    for doc in docs:
        doc_key = key or "_id"
        row: Dict[str, Any] = {}
        parent = {doc_key: _scalar(doc.get(doc_key))} if doc_key in doc else {}
        walk(doc, "", row, name, parent)
        tables[name].append(row)
    return {t: pd.DataFrame(rows) for t, rows in tables.items()}
