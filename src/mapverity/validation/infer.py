"""Infer a draft spec from known-good data.

Single-dataset profiling produces schema, null, key, domain, format, range, distribution and
row-ordering checks. When the pipeline's *source* is also given, mapverity mines
source-to-target relations: the join key, the row filter the pipeline applies, column
transformation rules (identity, case/whitespace normalisation, concatenation) and
group aggregates. Every inferred check is verified to pass on the data it came from.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..core.engine import run_check
from ..core.spec import CheckSpec, DatasetSpec, Spec
from .checks import Context, key_series

_ID_NAME = re.compile(r"(^|_)(id|key|number|num|no|nbr)$", re.IGNORECASE)
_DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$|^\d{1,2}/\d{1,2}/\d{4}$")


@dataclass
class InferOptions:
    max_categories: int = 25  # allowed_values only for columns at or below this cardinality
    max_category_ratio: float = 0.5  # ...and when distinct/rows is at most this
    range_slack: float = 0.10  # widen observed [min, max] by this fraction of the range
    distribution_z: float = 5.0  # mean_within tolerance in standard errors
    min_rows: int = 20  # minimum evidence before inferring relational rules
    row_rules: bool = True
    distribution: bool = True
    mine_mappings: bool = True
    mine_filter: bool = True
    mine_aggregates: bool = True
    sample_rows: int = 5000  # rows used for relation mining


# --------------------------------------------------------------------------- profiling


def infer_type(s: pd.Series) -> str:
    nn = s.dropna()
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "integer" if len(nn) and (nn == nn.round()).all() else "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if not len(nn):
        return "string"
    as_str = nn.astype(str)
    if as_str.str.match(_DATEISH).all() and pd.to_datetime(as_str, errors="coerce", format="mixed").notna().all():
        return "date"
    has_leading_zero = as_str.str.match(r"^0\d").any()
    num = pd.to_numeric(as_str, errors="coerce")
    if num.notna().all() and not has_leading_zero:
        return "integer" if (num == num.round()).all() else "number"
    return "string"


def _shape(value: str) -> str:
    out = []
    for ch in value:
        if ch.isdigit():
            out.append("9")
        elif ch.isupper():
            out.append("A")
        elif ch.islower():
            out.append("a")
        else:
            out.append(ch)
    return "".join(out)


def _shape_regex(shape: str) -> str:
    parts = []
    for cls, run in itertools.groupby(shape):
        n = len(list(run))
        token = {"9": r"\d", "A": "[A-Z]", "a": "[a-z]"}.get(cls, re.escape(cls))
        parts.append(token + (f"{{{n}}}" if n > 1 else ""))
    return "".join(parts)


def _fixed_format(s: pd.Series) -> Optional[str]:
    nn = s.dropna().astype(str)
    if not len(nn):
        return None
    shapes = nn.map(_shape).unique()
    if len(shapes) == 1 and any(c in shapes[0] for c in "9A"):
        return _shape_regex(shapes[0])
    return None


def _widen(lo, hi, slack: float, kind: str):
    span = hi - lo
    if kind == "date":
        pad = pd.Timedelta(days=max(1, int(span.days * slack)))
        return (lo - pad).strftime("%Y-%m-%d"), (hi + pad).strftime("%Y-%m-%d")
    pad = span * slack
    lo2, hi2 = lo - pad, hi + pad
    if float(lo) >= 0 and lo2 < 0:
        lo2 = 0  # don't let slack invent negative values for non-negative quantities
    return _py(lo2), _py(hi2)


def _py(v):
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return round(v, 6)
    return v


def _profile_checks(df: pd.DataFrame, dataset: str, opt: InferOptions) -> Tuple[List[CheckSpec], Dict[str, str]]:
    checks: List[CheckSpec] = []
    types = {c: infer_type(df[c]) for c in df.columns}
    n = len(df)
    checks.append(CheckSpec("schema", dataset=dataset, params={"columns": types, "strict": True}))
    numeric_like: Dict[str, pd.Series] = {}
    for col, typ in types.items():
        s = df[col]
        nulls = int(s.isna().sum())
        if nulls == 0:
            checks.append(CheckSpec("not_null", dataset=dataset, column=col))
        else:
            checks.append(
                CheckSpec(
                    "null_fraction",
                    dataset=dataset,
                    column=col,
                    params={"max": round(min(1.0, (nulls / n) * 1.5 + 0.01), 4)},
                )
            )
        nn = s.dropna()
        distinct = nn.nunique()
        if len(nn) and distinct == len(nn) and _ID_NAME.search(str(col)) and typ in ("string", "integer"):
            checks.append(CheckSpec("unique", dataset=dataset, column=col))
            continue  # keys: no domain/range/distribution checks
        is_categorical = (
            typ in ("string", "integer", "boolean")
            and 0 < distinct <= opt.max_categories
            and distinct / max(len(nn), 1) <= opt.max_category_ratio
        )
        if is_categorical:
            values = sorted(nn.astype(str).unique().tolist())
            checks.append(CheckSpec("allowed_values", dataset=dataset, column=col, params={"values": values}))
            continue
        if typ == "string":
            pattern = _fixed_format(s)
            if pattern:
                checks.append(CheckSpec("regex", dataset=dataset, column=col, params={"pattern": pattern}))
        elif typ in ("integer", "number") and len(nn):
            v = pd.to_numeric(nn, errors="coerce")
            lo, hi = _widen(v.min(), v.max(), opt.range_slack, "number")
            checks.append(CheckSpec("in_range", dataset=dataset, column=col, params={"min": lo, "max": hi}))
            numeric_like[col] = pd.to_numeric(s, errors="coerce")
            if opt.distribution and len(v) >= opt.min_rows and v.std() > 0:
                tol = max(opt.distribution_z * v.std() / np.sqrt(len(v)), 1e-3 * abs(v.mean()))
                checks.append(
                    CheckSpec(
                        "mean_within",
                        dataset=dataset,
                        column=col,
                        params={"mean": _py(v.mean()), "tolerance": _py(tol)},
                    )
                )
        elif typ == "date" and len(nn):
            v = pd.to_datetime(nn, errors="coerce", format="mixed")
            lo, hi = _widen(v.min(), v.max(), opt.range_slack, "date")
            checks.append(
                CheckSpec("in_range", dataset=dataset, column=col, params={"min": lo, "max": hi, "kind": "date"})
            )
            pattern = _fixed_format(s)
            if pattern:
                checks.append(CheckSpec("regex", dataset=dataset, column=col, params={"pattern": pattern}))
            numeric_like[col] = pd.to_datetime(s, errors="coerce", format="mixed")
    if opt.row_rules and n >= opt.min_rows:
        checks.extend(_ordering_rules(numeric_like, types, dataset, opt))
    return checks, types


def _ordering_rules(
    cols: Dict[str, pd.Series], types: Dict[str, str], dataset: str, opt: InferOptions
) -> List[CheckSpec]:
    """Mine `a <= b` invariants between comparable columns whose ranges overlap.

    Overlap matters: if every `a` is below every `b` the rule is implied by the ranges and adds nothing.
    """
    out = []
    names = list(cols)
    for a, b in itertools.permutations(names, 2):
        ta, tb = types[a], types[b]
        if (ta == "date") != (tb == "date"):
            continue
        sa, sb = cols[a], cols[b]
        both = sa.notna() & sb.notna()
        if both.sum() < opt.min_rows:
            continue
        x, y = sa[both], sb[both]
        if not (x <= y).all() or not (x < y).any():
            continue
        if x.max() <= y.min():
            continue  # implied by ranges
        if ta == "date":
            expr = f'date(col("{a}")) <= date(col("{b}"))'
        else:
            expr = f'num(col("{a}")) <= num(col("{b}"))'
        out.append(
            CheckSpec("row_rule", dataset=dataset, name=f"order:{dataset}:{a}<={b}", params={"expression": expr})
        )
    return out


# --------------------------------------------------------------------------- source-to-target mining


def detect_key(source: pd.DataFrame, target: pd.DataFrame) -> Optional[str]:
    common = [c for c in target.columns if c in source.columns]
    ranked = sorted(common, key=lambda c: (not _ID_NAME.search(str(c)), common.index(c)))
    for c in ranked:
        if target[c].isna().any():
            continue
        t = key_series(target, c)
        if len(t) and t.is_unique and set(t) <= set(key_series(source, c)):
            return c
    return None


def mine_filter(source: pd.DataFrame, kept_keys: set, key: str, opt: InferOptions) -> Optional[str]:
    """Greedily find a conjunction of simple predicates selecting exactly the source rows the
    pipeline kept. Returns an expression or None when no exact filter is found."""
    kept = key_series(source, key).isin(kept_keys)
    if kept.all():
        return None
    preds: List[Tuple[str, pd.Series]] = []
    for col in source.columns:
        if col == key:
            continue
        s = source[col]
        if s.isna().any():
            preds.append((f'notnull(col("{col}"))', s.notna()))
        nn = s.dropna()
        if 0 < nn.nunique() <= opt.max_categories:
            kept_vals = sorted(s[kept].dropna().astype(str).unique().tolist())
            mask = s.astype(str).isin(kept_vals)
            preds.append((f'isin(str(col("{col}")), {kept_vals!r})', mask))
        typ = infer_type(s)
        if typ in ("integer", "number"):
            v = pd.to_numeric(s, errors="coerce")
            lo, hi = v[kept].min(), v[kept].max()
            preds.append((f'num(col("{col}")) >= {_py(lo)!r}', v >= lo))
            preds.append((f'num(col("{col}")) <= {_py(hi)!r}', v <= hi))
    # pairwise date ordering, e.g. eligibility_end >= as_of
    dates = [c for c in source.columns if c != key and infer_type(source[c]) == "date"]
    for a, b in itertools.permutations(dates, 2):
        da = pd.to_datetime(source[a], errors="coerce", format="mixed")
        db = pd.to_datetime(source[b], errors="coerce", format="mixed")
        mask = (da >= db) | da.isna()
        preds.append((f'(date(col("{a}")) >= date(col("{b}"))) | isnull(col("{a}"))', mask))
    current = pd.Series(True, index=source.index)
    chosen: List[str] = []
    while True:
        excess = current & ~kept
        if not excess.any():
            break
        best = None
        for expr, mask in preds:
            mask = mask.fillna(False).astype(bool)
            if (kept & ~mask).any():
                continue  # would drop a row the pipeline kept
            removed = int((excess & ~mask).sum())
            if removed and (best is None or removed > best[0]):
                best = (removed, expr, mask)
        if best is None:
            return None
        chosen.append(best[1])
        current &= best[2]
    return " & ".join(f"({c})" for c in chosen) if chosen else None


_STRING_TRANSFORMS = [
    ("{c}", lambda s: s),
    ('strip(col("{c}"))', lambda s: s.astype("string").str.strip()),
    ('upper(col("{c}"))', lambda s: s.astype("string").str.upper()),
    ('lower(col("{c}"))', lambda s: s.astype("string").str.lower()),
    ('title(col("{c}"))', lambda s: s.astype("string").str.title()),
    ('upper(strip(col("{c}")))', lambda s: s.astype("string").str.strip().str.upper()),
]


def _equal(actual: pd.Series, expected: pd.Series) -> bool:
    a = actual.reset_index(drop=True)
    e = expected.reset_index(drop=True)
    an, en = pd.to_numeric(a, errors="coerce"), pd.to_numeric(e, errors="coerce")
    if an.notna().all() and en.notna().all() and a.notna().all():
        return bool(((an - en).abs() <= 1e-9).all())
    both_null = a.isna() & e.isna()
    eq = (a.astype("string") == e.astype("string")).fillna(False)
    return bool((eq | both_null).all())


def mine_mappings(
    source: pd.DataFrame, target: pd.DataFrame, key: str, opt: InferOptions, source_name: str, target_name: str
) -> List[CheckSpec]:
    src = source.drop_duplicates(key).copy()
    src["__k__"] = key_series(src, key)
    tgt = target.head(opt.sample_rows).add_prefix("t__")
    tgt["__k__"] = key_series(tgt, "t__" + key)
    joined = tgt.merge(src, on="__k__", how="inner")
    if len(joined) < opt.min_rows:
        return []
    out: List[CheckSpec] = []
    string_src = [c for c in source.columns if c != key and infer_type(source[c]) == "string"]
    for tcol in target.columns:
        if tcol == key:
            continue
        actual = joined["t__" + tcol]
        found = None
        # identity / normalisation on any single source column, preferring the same name
        candidates = sorted([c for c in source.columns if c != key], key=lambda c: c != tcol)
        for scol in candidates:
            for template, fn in _STRING_TRANSFORMS:
                try:
                    if _equal(actual, fn(joined[scol])):
                        found = template.format(c=scol) if template != "{c}" else f'col("{scol}")'
                        break
                except Exception:  # noqa: S112 - a transform that cannot apply is simply not a match
                    continue
            if found:
                break
        if not found and infer_type(target[tcol]) == "string":
            for a, b in itertools.permutations(string_src, 2):
                exp = joined[a].astype("string") + " " + joined[b].astype("string")
                if _equal(actual, exp):
                    found = f'concat(col("{a}"), col("{b}"))'
                    break
        if found:
            out.append(
                CheckSpec(
                    "mapping",
                    dataset=target_name,
                    column=tcol,
                    params={"source": source_name, "key": key, "expression": found},
                )
            )
    return out


def mine_group_aggregates(
    source: pd.DataFrame,
    target: pd.DataFrame,
    key: str,
    opt: InferOptions,
    source_name: str,
    target_name: str,
    source_filter: Optional[str],
) -> List[CheckSpec]:
    from ..core.expr import evaluate

    src = source
    if source_filter:
        src = source[evaluate(source_filter, source).astype("boolean").fillna(False).astype(bool)]
    if src[key].is_unique:
        return []  # not an aggregation pipeline
    out: List[CheckSpec] = []
    groups = key_series(src, key)
    num_src = [c for c in src.columns if c != key and infer_type(src[c]) in ("integer", "number")]
    tgt = target.set_index(key_series(target, key))
    for tcol in target.columns:
        if tcol == key or infer_type(target[tcol]) not in ("integer", "number"):
            continue
        actual = pd.to_numeric(tgt[tcol], errors="coerce")
        tried = [("count", "1")] + [(agg, f'num(col("{c}"))') for c in num_src for agg in ("sum", "max", "min", "mean")]
        for agg, expr in tried:
            vals = (
                pd.Series(1, index=src.index)
                if expr == "1"
                else pd.to_numeric(src[expr.split('"')[1]], errors="coerce")
            )
            expected = vals.groupby(groups).agg(agg).reindex(actual.index)
            if expected.notna().all() and ((actual - expected).abs() <= 1e-6 * np.maximum(1, expected.abs())).all():
                params = {
                    "source": source_name,
                    "key": key,
                    "expression": expr,
                    "agg": agg,
                    "tolerance": 0.01 if agg != "count" else 0,
                }
                if source_filter:
                    params["source_filter"] = source_filter
                out.append(CheckSpec("group_aggregate", dataset=target_name, column=tcol, params=params))
                break
    return out


def infer_spec(
    target: pd.DataFrame,
    source: Optional[pd.DataFrame] = None,
    key: Optional[str] = None,
    name: str = "inferred",
    target_name: str = "target",
    source_name: str = "source",
    options: Optional[InferOptions] = None,
) -> Spec:
    """Infer a spec from a known-good target (and optionally its source)."""
    opt = options or InferOptions()
    checks, _ = _profile_checks(target, target_name, opt)
    datasets = {target_name: DatasetSpec()}
    if source is not None:
        datasets[source_name] = DatasetSpec()
        key = key or detect_key(source, target)
        if key is not None:
            src_filter = None
            if opt.mine_filter:
                src_filter = mine_filter(source, set(key_series(target, key)), key, opt)
            aggregated = not source[key].is_unique
            if not aggregated:
                params = {"source": source_name, "target": target_name}
                if src_filter:
                    params["source_filter"] = src_filter
                checks.append(CheckSpec("row_count_match", dataset=target_name, params=dict(params)))
            km = {"source": source_name, "target": target_name, "key": key}
            if src_filter or key_series(source, key).isin(set(key_series(target, key))).all():
                km["mode"] = "equal"
                if src_filter:
                    km["source_filter"] = src_filter
            else:
                km["mode"] = "subset"
            checks.append(CheckSpec("key_match", dataset=target_name, params=km))
            if opt.mine_mappings and not aggregated:
                checks.extend(mine_mappings(source, target, key, opt, source_name, target_name))
            if opt.mine_aggregates and aggregated:
                checks.extend(mine_group_aggregates(source, target, key, opt, source_name, target_name, src_filter))
    spec = Spec(name=name, datasets=datasets, checks=checks)
    # keep only checks that hold on the evidence they were inferred from
    data = {target_name: target}
    if source is not None:
        data[source_name] = source
    ctx = Context(data)
    spec.checks = [c for c in spec.checks if run_check(ctx, c).passed]
    return spec
