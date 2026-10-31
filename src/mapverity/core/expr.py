"""Small vectorised expression language used by `row_rule` and `mapping` checks.

Expressions are ordinary Python expressions evaluated over pandas Series. Column names
are available directly when they are valid identifiers, and always through ``col("name")``.

Specs are treated as trusted configuration (like a Makefile or a CI file): builtins are
disabled, but do not load specs from untrusted sources.
"""

from __future__ import annotations

import keyword
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _s(x: Any) -> pd.Series:
    return x if isinstance(x, pd.Series) else pd.Series(x)


def _date(x):
    return pd.to_datetime(x, errors="coerce", format="mixed") if isinstance(x, pd.Series) else pd.to_datetime(x)


def _num(x):
    return pd.to_numeric(x, errors="coerce")


def _str(x):
    if isinstance(x, pd.Series):
        return x.astype("string")
    return str(x)


def _concat(*parts, sep: str = " "):
    series = [p for p in parts if isinstance(p, pd.Series)]
    if not series:
        return sep.join(str(p) for p in parts)
    index = series[0].index
    out = None
    for p in parts:
        p = (
            p.astype("string")
            if isinstance(p, pd.Series)
            else pd.Series([str(p)] * len(index), index=index, dtype="string")
        )
        out = p if out is None else out + sep + p
    return out


def _coalesce(*parts):
    out = None
    for p in parts:
        if out is None:
            out = p
        elif isinstance(out, pd.Series):
            out = out.where(out.notna(), p)
    return out


def _years_between(a, b):
    a, b = _date(a), _date(b)
    years = b.dt.year - a.dt.year
    before_bday = (b.dt.month < a.dt.month) | ((b.dt.month == a.dt.month) & (b.dt.day < a.dt.day))
    return years - before_bday.astype("Int64")


def _lookup(x, table: Dict[Any, Any], default=None):
    """Code translation, e.g. lookup(gender_cd, {"M": "MALE", "F": "FEMALE"}, "UNKNOWN")."""
    s = _s(x).astype("string").str.strip()
    keys = {str(k): v for k, v in table.items()}
    out = s.map(lambda v: keys.get(v, default) if v is not pd.NA else default)
    return out.astype(object)


def _to_date(x, fmt: Optional[str] = None):
    """Reformat a date to ISO YYYY-MM-DD; `fmt` is the source format, e.g. '%Y%m%d'."""
    s = _s(x)
    if fmt:
        s = s.astype("string").str.strip()
        d = pd.to_datetime(s, format=fmt, errors="coerce")
    else:
        d = pd.to_datetime(s, errors="coerce", format="mixed")
    return d.dt.strftime("%Y-%m-%d").where(d.notna(), None).astype(object)


def _substr(x, start: int, length: Optional[int] = None):
    """1-based substring (COBOL/SQL style)."""
    s = _str(x)
    return s.str.slice(start - 1, None if length is None else start - 1 + length)


HELPERS: Dict[str, Any] = {
    "lookup": _lookup,
    "to_date": _to_date,
    "substr": _substr,
    "lpad": lambda x, n, ch="0": _str(x).str.pad(n, side="left", fillchar=ch),
    "rpad": lambda x, n, ch=" ": _str(x).str.pad(n, side="right", fillchar=ch),
    "replace": lambda x, a, b: _str(x).str.replace(a, b, regex=False),
    "int": lambda x: _num(x).round().astype("Int64") if isinstance(x, pd.Series) else int(x),
    "float": lambda x: _num(x).astype(float) if isinstance(x, pd.Series) else float(x),
    "date": _date,
    "num": _num,
    "str": _str,
    "upper": lambda x: _str(x).str.upper(),
    "lower": lambda x: _str(x).str.lower(),
    "strip": lambda x: _str(x).str.strip(),
    "title": lambda x: _str(x).str.title(),
    "length": lambda x: _str(x).str.len(),
    "concat": _concat,
    "coalesce": _coalesce,
    "isnull": lambda x: _s(x).isna(),
    "notnull": lambda x: _s(x).notna(),
    "isin": lambda x, values: _s(x).isin(values),
    "where": lambda cond, a, b: pd.Series(np.where(cond, a, b), index=cond.index),
    "abs": lambda x: x.abs() if isinstance(x, pd.Series) else abs(x),
    "round": lambda x, n=0: x.round(n) if isinstance(x, pd.Series) else round(x, n),
    "years_between": _years_between,
    "True": True,
    "False": False,
    "None": None,
}


def namespace(df: pd.DataFrame) -> Dict[str, Any]:
    ns: Dict[str, Any] = dict(HELPERS)
    ns["col"] = lambda name: df[name]
    for c in df.columns:
        if isinstance(c, str) and c.isidentifier() and not keyword.iskeyword(c) and c not in HELPERS:
            ns[c] = df[c]
    return ns


def referenced_columns(expression: str, df: pd.DataFrame) -> list:
    """Columns an expression reads, via col("x") or a bare identifier."""
    import ast

    cols = []
    for node in ast.walk(ast.parse(expression, mode="eval")):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "col"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in df.columns
        ):
            cols.append(node.args[0].value)
        elif isinstance(node, ast.Name) and node.id in df.columns and node.id not in HELPERS:
            cols.append(node.id)
    return list(dict.fromkeys(cols))


def evaluate(expression: str, df: pd.DataFrame) -> Any:
    code = compile(expression, "<mapverity-expression>", "eval")
    result = eval(code, {"__builtins__": {}}, namespace(df))  # noqa: S307 - trusted spec input, builtins disabled
    if not isinstance(result, pd.Series):
        result = pd.Series([result] * len(df), index=df.index)
    return result
