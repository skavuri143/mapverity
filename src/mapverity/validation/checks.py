"""Built-in checks. Each check returns a CheckOutcome; register new ones with @register."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..core.expr import evaluate, referenced_columns
from ..core.spec import CheckSpec

SAMPLE_SIZE = 5


@dataclass
class CheckOutcome:
    failed: int = 0
    total: int = 0
    message: str = ""
    sample: List[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass
class Context:
    data: Dict[str, pd.DataFrame]

    def frame(self, name: Optional[str]) -> pd.DataFrame:
        if name is None:
            if len(self.data) == 1:
                return next(iter(self.data.values()))
            raise ValueError("check has no 'dataset' and more than one dataset is loaded")
        if name not in self.data:
            raise KeyError(f"dataset '{name}' not loaded (have: {sorted(self.data)})")
        return self.data[name]


CheckFn = Callable[[Context, CheckSpec], CheckOutcome]
REGISTRY: Dict[str, CheckFn] = {}


def register(name: str):
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = fn
        return fn

    return deco


def _column(ctx: Context, check: CheckSpec) -> pd.Series:
    df = ctx.frame(check.dataset)
    if check.column is None:
        raise ValueError(f"check '{check.id}' needs a 'column'")
    if check.column not in df.columns:
        raise KeyError(f"column '{check.column}' not found in dataset '{check.dataset}'")
    return df[check.column]


def _sample(values) -> List[Any]:
    out = []
    for v in list(values)[:SAMPLE_SIZE]:
        if isinstance(v, (np.generic,)):
            v = v.item()
        elif isinstance(v, pd.Timestamp):
            v = v.isoformat()
        out.append(None if (isinstance(v, float) and np.isnan(v)) else v)
    return out


def _fail_from_mask(series: pd.Series, bad: pd.Series, message: str) -> CheckOutcome:
    bad = bad.fillna(False).astype(bool)
    n = int(bad.sum())
    return CheckOutcome(
        failed=n,
        total=int(len(series)),
        message=message if n else "",
        sample=_sample(series[bad]) if n else [],
    )


# --------------------------------------------------------------------------- single-column


_TYPE_PARSERS = {
    "integer": lambda s: (pd.to_numeric(s, errors="coerce"), lambda p: p.notna() & (p == p.round())),
    "float": lambda s: (pd.to_numeric(s, errors="coerce"), lambda p: p.notna()),
    "number": lambda s: (pd.to_numeric(s, errors="coerce"), lambda p: p.notna()),
    "date": lambda s: (pd.to_datetime(s, errors="coerce", format="mixed"), lambda p: p.notna()),
    "datetime": lambda s: (pd.to_datetime(s, errors="coerce", format="mixed"), lambda p: p.notna()),
    "boolean": lambda s: (
        s.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}),
        lambda p: p.notna(),
    ),
    "string": lambda s: (s, lambda p: p.notna()),
}


@register("schema")
def check_schema(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Columns exist and non-null values parse as the declared type.

    params: columns: {name: type}, strict: bool (no extra columns allowed)
    """
    df = ctx.frame(check.dataset)
    columns: Dict[str, str] = check.params.get("columns", {})
    problems: List[str] = []
    failed = 0
    for col, typ in columns.items():
        if col not in df.columns:
            problems.append(f"missing column '{col}'")
            failed += len(df) or 1
            continue
        if typ not in _TYPE_PARSERS:
            raise ValueError(f"unknown type '{typ}' for column '{col}'")
        s = df[col]
        present = s.notna()
        parsed, ok = _TYPE_PARSERS[typ](s)
        bad = present & ~ok(parsed)
        n = int(bad.sum())
        if n:
            problems.append(f"'{col}': {n} value(s) not {typ}, e.g. {_sample(s[bad])[:3]}")
            failed += n
    if check.params.get("strict"):
        extra = [c for c in df.columns if c not in columns]
        if extra:
            problems.append(f"unexpected column(s) {extra}")
            failed += len(extra)
    return CheckOutcome(failed=failed, total=len(df), message="; ".join(problems))


@register("not_null")
def check_not_null(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """No nulls in `column`."""
    s = _column(ctx, check)
    return _fail_from_mask(s, s.isna(), f"{int(s.isna().sum())} null value(s) in '{check.column}'")


@register("null_fraction")
def check_null_fraction(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Share of nulls in `column` is at most params `max` (0..1)."""
    s = _column(ctx, check)
    frac = float(s.isna().mean()) if len(s) else 0.0
    limit = float(check.params.get("max", 0.0))
    if frac > limit:
        return CheckOutcome(
            failed=int(s.isna().sum()), total=len(s), message=f"null fraction {frac:.4f} exceeds {limit:.4f}"
        )
    return CheckOutcome(total=len(s))


@register("unique")
def check_unique(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Values (or composite keys) are unique. Single column via `column`, composite via params `columns`."""
    df = ctx.frame(check.dataset)
    cols = check.params.get("columns") or [check.column]
    dup = df.duplicated(subset=cols, keep=False)
    keys = df.loc[dup, cols]
    sample = keys.head(SAMPLE_SIZE).to_dict("records") if len(cols) > 1 else _sample(keys[cols[0]])
    n = int(dup.sum())
    return CheckOutcome(
        failed=n, total=len(df), message=f"{n} row(s) share a duplicate {cols}" if n else "", sample=sample if n else []
    )


@register("allowed_values")
def check_allowed_values(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Non-null values are in a fixed set. params: values: [...]; nulls are ignored (pair with not_null)."""
    s = _column(ctx, check)
    allowed = check.params.get("values", [])
    as_str = {str(v) for v in allowed}
    bad = s.notna() & ~s.astype("string").isin(as_str)
    return _fail_from_mask(s, bad, f"value(s) outside allowed set in '{check.column}'")


@register("regex")
def check_regex(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Non-null values fully match a regular expression. params: pattern"""
    s = _column(ctx, check)
    pattern = re.compile(check.params["pattern"])
    present = s.notna()
    bad = present & ~s.astype("string").str.fullmatch(pattern).fillna(False)
    return _fail_from_mask(s, bad, f"value(s) do not match /{pattern.pattern}/ in '{check.column}'")


@register("in_range")
def check_in_range(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Values lie within bounds. params: min, max (either optional), kind: number|date. Unparseable values fail."""
    s = _column(ctx, check)
    kind = check.params.get("kind", "number")
    lo, hi = check.params.get("min"), check.params.get("max")
    if kind == "date":
        v = pd.to_datetime(s, errors="coerce", format="mixed")
        lo = pd.Timestamp(lo) if lo is not None else None
        hi = pd.Timestamp(hi) if hi is not None else None
    else:
        v = pd.to_numeric(s, errors="coerce")
    bad = s.notna() & v.isna()  # unparseable values are out of range
    if lo is not None:
        bad |= v < lo
    if hi is not None:
        bad |= v > hi
    return _fail_from_mask(s, bad, f"value(s) outside [{lo}, {hi}] in '{check.column}'")


@register("mean_within")
def check_mean_within(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Distribution guard. params: mean, tolerance (absolute)"""
    s = pd.to_numeric(_column(ctx, check), errors="coerce")
    observed = float(s.mean())
    expected, tol = float(check.params["mean"]), float(check.params["tolerance"])
    if not np.isfinite(observed) or abs(observed - expected) > tol:
        return CheckOutcome(
            failed=1, total=1, message=f"mean {observed:.4f} differs from {expected:.4f} by more than {tol:.4f}"
        )
    return CheckOutcome(total=1)


@register("row_rule")
def check_row_rule(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Every row satisfies a boolean expression. Rows where it evaluates to null pass.

    params: expression
    """
    df = ctx.frame(check.dataset)
    expression = check.params["expression"]
    result = evaluate(expression, df)
    bad = ~result.astype("boolean").fillna(True)
    refs = referenced_columns(expression, df)
    if refs and not check.params.get("nulls_fail", False):
        bad &= ~df[refs].isna().any(axis=1)  # rows with nulls in referenced columns are not judged
    n = int(bad.sum())
    sample = df.loc[bad.astype(bool)].head(SAMPLE_SIZE).to_dict("records") if n else []
    return CheckOutcome(
        failed=n, total=len(df), message=f"{n} row(s) violate: {check.params['expression']}" if n else "", sample=sample
    )


# --------------------------------------------------------------------------- cross-dataset


_SOURCE_CACHE: Dict[tuple, tuple] = {}


def _cached(key: tuple, frame: pd.DataFrame, compute):
    """Memoise work that depends only on an (unchanged) source frame. Identity-checked, so a
    different or mutated frame is never served stale results."""
    hit = _SOURCE_CACHE.get(key)
    if hit is not None and hit[0] is frame:
        return hit[1]
    value = compute()
    if len(_SOURCE_CACHE) > 256:
        _SOURCE_CACHE.clear()
    _SOURCE_CACHE[key] = (frame, value)
    return value


def _column_or_expr(df: pd.DataFrame, expr: str) -> pd.Series:
    """A column name (even one with dots, like `name.first`) or an expression."""
    return df[expr] if expr in df.columns else evaluate(expr, df)


def _filtered(df: pd.DataFrame, expression: Optional[str]) -> pd.DataFrame:
    if not expression:
        return df
    mask = evaluate(expression, df).astype("boolean").fillna(False).astype(bool)
    return df[mask]


def _keys(check: CheckSpec):
    """Return (source_key, target_key); each is a column name or a list (composite key)."""
    key = check.params.get("key")
    left = check.params.get("source_key", key)
    right = check.params.get("target_key", key)
    if left is None or right is None:
        raise ValueError(f"check '{check.id}' needs 'key' (or source_key/target_key)")
    return left, right


def key_series(df: pd.DataFrame, key) -> pd.Series:
    """String key for a column or a composite key (values joined with '|')."""
    cols = [key] if isinstance(key, str) else list(key)
    if not cols:
        raise ValueError("a key needs at least one column")
    out: Optional[pd.Series] = None
    for c in cols:
        # a key part is a column name, or an expression such as to_date(col("START_DT"), "%Y%m%d")
        v = df[c] if c in df.columns else evaluate(c, df)
        # normalise numerics so 7 and 7.0 and '7' match
        num = pd.to_numeric(v, errors="coerce")
        s = v.astype("string")
        integral = num.notna() & (num == num.round())
        s = s.where(~integral, num.round().astype("Int64").astype("string"))
        if pd.api.types.is_datetime64_any_dtype(v):
            s = v.dt.strftime("%Y-%m-%d").astype("string")
        s = s.fillna("<null>")  # no trimming: a padded key is a defect, not a match
        out = s if out is None else out + "|" + s
    assert out is not None
    return out


@register("row_count_match")
def check_row_count_match(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Target row count equals (filtered) source row count.

    params: source, target, source_filter (expression), tolerance (rows), tolerance_pct
    """
    src = _filtered(ctx.frame(check.params["source"]), check.params.get("source_filter"))
    tgt = ctx.frame(check.params.get("target", check.dataset))
    diff = abs(len(tgt) - len(src))
    allowed = max(float(check.params.get("tolerance", 0)), float(check.params.get("tolerance_pct", 0)) * len(src) / 100)
    if diff > allowed:
        return CheckOutcome(
            failed=diff, total=len(src), message=f"target has {len(tgt)} rows, expected {len(src)} (diff {diff})"
        )
    return CheckOutcome(total=len(src))


@register("key_match")
def check_key_match(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Target keys vs (filtered) source keys.

    params: source, target, key | source_key/target_key, source_filter, mode: equal|subset
    """
    raw_src = ctx.frame(check.params["source"])
    sf = check.params.get("source_filter")
    tgt = ctx.frame(check.params.get("target", check.dataset))
    lk, rk = _keys(check)
    parent = check.params.get("parent")
    if parent:
        # array tables: only source records whose parent document was loaded are expected
        loaded = set(key_series(ctx.frame(parent["dataset"]), parent["key"]))
        src = _filtered(raw_src, sf)
        src = src[key_series(src, parent["source_key"]).isin(loaded)]
        sk = set(key_series(src, lk))
    else:
        sk = _cached(("keys", id(raw_src), repr(lk), sf), raw_src, lambda: set(key_series(_filtered(raw_src, sf), lk)))
    tk = set(key_series(tgt, rk))
    extra = sorted(tk - sk)
    missing = sorted(sk - tk) if check.params.get("mode", "equal") == "equal" else []
    n = len(extra) + len(missing)
    msg = []
    if missing:
        msg.append(f"{len(missing)} source key(s) missing from target")
    if extra:
        msg.append(f"{len(extra)} target key(s) not in source")
    return CheckOutcome(failed=n, total=len(sk | tk), message="; ".join(msg), sample=(missing + extra)[:SAMPLE_SIZE])


@register("referential")
def check_referential(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Foreign key: non-null values of dataset.column exist in params ref_dataset.ref_column."""
    s = _column(ctx, check)
    ref = ctx.frame(check.params["ref_dataset"])[check.params["ref_column"]]
    bad = s.notna() & ~s.astype(str).isin(set(ref.dropna().astype(str)))
    return _fail_from_mask(s, bad, f"orphan value(s) in '{check.column}'")


def _values_equal(
    actual: pd.Series, expected: pd.Series, tolerance: float, case_insensitive: bool, ignore_whitespace: bool = False
) -> pd.Series:
    """Exact comparison by default: padding and case differences are real defects in ETL output."""
    both_null = actual.isna() & expected.isna()
    a_num, e_num = pd.to_numeric(actual, errors="coerce"), pd.to_numeric(expected, errors="coerce")
    is_text = actual.map(lambda v: isinstance(v, str)) | expected.map(lambda v: isinstance(v, str))
    numeric = a_num.notna() & e_num.notna() & ~is_text
    eq = pd.Series(False, index=actual.index)
    eps = 1e-9 * np.maximum(1.0, e_num[numeric].abs())  # float noise: 0.1 vs 0.10000000000000853
    eq[numeric] = (a_num[numeric] - e_num[numeric]).abs() <= tolerance + eps
    rest = ~numeric & actual.notna() & expected.notna()
    if rest.any():
        a = actual[rest].astype("string")
        e = expected[rest].astype("string")
        if pd.api.types.is_datetime64_any_dtype(expected):
            e = expected[rest].dt.strftime("%Y-%m-%d").astype("string")
            a = pd.to_datetime(actual[rest], errors="coerce", format="mixed").dt.strftime("%Y-%m-%d").astype("string")
        if ignore_whitespace:
            a, e = a.str.strip(), e.str.strip()
        if case_insensitive:
            a, e = a.str.lower(), e.str.lower()
        eq[rest] = (a == e).fillna(False).astype(bool)
    return eq | both_null


@register("mapping")
def check_mapping(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Source-to-target transformation rule: for matched keys, target.column == f(source row).

    params: source, target (defaults to `dataset`), key | source_key/target_key,
            expression (over source columns), tolerance (numeric), case_insensitive
    """
    src = ctx.frame(check.params["source"])
    tgt = ctx.frame(check.params.get("target", check.dataset))
    lk, rk = _keys(check)
    col = check.column
    if col not in tgt.columns:
        raise KeyError(f"column '{col}' not found in target '{check.params.get('target', check.dataset)}'")
    left = pd.DataFrame({"__key__": key_series(tgt, rk), "__actual__": tgt[col]})
    sf, expression = check.params.get("source_filter"), check.params["expression"]

    def expected_by_key():
        s = _filtered(src, sf)
        frame = pd.DataFrame({"__key__": key_series(s, lk), "__expected__": evaluate(expression, s)})
        return frame.drop_duplicates("__key__")

    right = _cached(("mapping", id(src), repr(lk), sf, expression), src, expected_by_key)
    joined = left.merge(right, on="__key__", how="inner")
    if joined.empty:
        return CheckOutcome(total=0)
    expected = joined["__expected__"]
    eq = _values_equal(
        joined["__actual__"],
        expected,
        float(check.params.get("tolerance", 1e-9)),
        bool(check.params.get("case_insensitive", False)),
        bool(check.params.get("ignore_whitespace", False)),
    )
    bad = ~eq
    n = int(bad.sum())
    sample = [
        {"key": k, "actual": _sample([a])[0], "expected": _sample([e])[0]}
        for k, a, e in zip(
            joined.loc[bad, "__key__"].head(SAMPLE_SIZE),
            joined.loc[bad, "__actual__"].head(SAMPLE_SIZE),
            expected[bad].head(SAMPLE_SIZE),
        )
    ]
    return CheckOutcome(
        failed=n,
        total=len(joined),
        message=f"{n} row(s) where '{col}' != {check.params['expression']}" if n else "",
        sample=sample,
    )


_AGG = {"sum": "sum", "mean": "mean", "min": "min", "max": "max", "count": "count"}


@register("group_aggregate")
def check_group_aggregate(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Aggregation pipelines: target.column == agg(source expression) grouped by key.

    params: source, target, key | source_key/target_key, expression, agg, tolerance, source_filter
    """
    agg = _AGG[check.params.get("agg", "sum")]
    src = _filtered(ctx.frame(check.params["source"]), check.params.get("source_filter"))
    tgt = ctx.frame(check.params.get("target", check.dataset))
    lk, rk = _keys(check)
    values = pd.to_numeric(evaluate(check.params.get("expression", "1"), src), errors="coerce")
    expected = values.groupby(key_series(src, lk)).agg(agg).rename("__expected__")
    left = pd.DataFrame({"__key__": key_series(tgt, rk), "__actual__": tgt[check.column]})
    joined = left.merge(expected, left_on="__key__", right_index=True, how="inner")
    if joined.empty:
        return CheckOutcome(total=0)
    eq = _values_equal(joined["__actual__"], joined["__expected__"], float(check.params.get("tolerance", 1e-6)), False)
    bad = ~eq
    n = int(bad.sum())
    bad_rows = joined.loc[bad, ["__key__", "__actual__", "__expected__"]].head(SAMPLE_SIZE)
    sample: List[dict[str, Any]] = [
        {"key": r[0], "actual": _sample([r[1]])[0], "expected": _sample([r[2]])[0]}
        for r in bad_rows.itertuples(index=False)
    ]
    return CheckOutcome(
        failed=n,
        total=len(joined),
        message=f"{n} group(s) where '{check.column}' != {agg}({check.params.get('expression', '1')})" if n else "",
        sample=sample,
    )


@register("aggregate_match")
def check_aggregate_match(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """Reconcile an aggregate between source and target (e.g. total paid amount).

    params: source, target, source_expression, target_expression (or column), agg, tolerance_pct,
            source_filter
    """
    agg = _AGG[check.params.get("agg", "sum")]
    src = _filtered(ctx.frame(check.params["source"]), check.params.get("source_filter"))
    tgt = ctx.frame(check.params.get("target", check.dataset))
    s_expr = check.params.get("source_expression", check.column)
    t_expr = check.params.get("target_expression", check.column)
    if not s_expr or not t_expr:
        raise ValueError(f"check '{check.id}' needs `column` or source_expression/target_expression")
    sv = pd.to_numeric(_column_or_expr(src, s_expr), errors="coerce").agg(agg)
    tv = pd.to_numeric(_column_or_expr(tgt, t_expr), errors="coerce").agg(agg)
    tol = float(check.params.get("tolerance_pct", 0.0)) / 100 * max(abs(sv), 1e-9) + 1e-6
    if not (np.isfinite(sv) and np.isfinite(tv)) or abs(sv - tv) > tol:
        return CheckOutcome(failed=1, total=1, message=f"{agg}: source={sv:.4f} target={tv:.4f}")
    return CheckOutcome(total=1)


@register("control_total")
def check_control_total(ctx: Context, check: CheckSpec) -> CheckOutcome:
    """File control totals: a trailer/header value equals an aggregate over the detail records.

    params: control_dataset, control_column (the trailer value), dataset + expression/column (details),
            agg: count|sum|min|max (default count), tolerance
    """
    ctl = ctx.frame(check.params["control_dataset"])
    if len(ctl) != 1:
        return CheckOutcome(failed=1, total=1, message=f"expected exactly one control record, found {len(ctl)}")
    expected = pd.to_numeric(ctl[check.params["control_column"]], errors="coerce").iloc[0]
    df = ctx.frame(check.dataset)
    agg = check.params.get("agg", "count")
    if agg == "count":
        actual = float(len(df))
    else:
        values = pd.to_numeric(
            evaluate(check.params.get("expression") or f'col("{check.column}")', df), errors="coerce"
        )
        actual = float(values.agg(agg))
    tol = float(check.params.get("tolerance", 0.005))
    if not np.isfinite(expected) or abs(actual - float(expected)) > tol:
        return CheckOutcome(
            failed=1, total=1, message=f"{agg} of detail records = {actual:,.2f}, control record says {expected:,.2f}"
        )
    return CheckOutcome(total=1)
