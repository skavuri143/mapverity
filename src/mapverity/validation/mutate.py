"""Data mutation testing: measure how good a spec is at catching pipeline faults.

Classic mutation testing seeds small faults into *code* and asks whether the test suite
notices. mapverity seeds faults into a pipeline's *output data* -- the observable symptom of
an ETL defect -- and asks whether the spec notices. Each operator models a fault class that
data engineers routinely hit (dropped rows from a bad join, nulls from a broken lookup,
truncation from a too-narrow column, timezone/date-offset bugs, swapped columns, ...).

A mutant is *killed* when at least one check that passed on the original data fails on the
mutant. Mutation score = killed / (non-equivalent mutants). Surviving mutants point at the
blind spots of a spec.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from ..core.engine import run_check
from ..core.spec import Spec
from .checks import Context
from .infer import infer_type

Operator = Callable[[pd.DataFrame, str, np.random.Generator, float], Optional[pd.DataFrame]]


@dataclass
class OperatorInfo:
    name: str
    fault_class: str
    scope: str  # column | row | table
    fn: Operator
    applies_to: tuple = ("string", "integer", "number", "date", "boolean")


OPERATORS: Dict[str, OperatorInfo] = {}


def operator(name: str, fault_class: str, scope: str = "column", applies_to=None):
    def deco(fn: Operator) -> Operator:
        OPERATORS[name] = OperatorInfo(
            name, fault_class, scope, fn, tuple(applies_to) if applies_to else OperatorInfo.applies_to
        )
        return fn

    return deco


def _rows(df: pd.DataFrame, rng: np.random.Generator, rate: float, eligible: Optional[pd.Series] = None) -> np.ndarray:
    idx = df.index if eligible is None else df.index[eligible.to_numpy()]
    if len(idx) == 0:
        return np.array([], dtype=object)
    k = max(1, int(round(len(df) * rate)))
    k = min(k, len(idx))
    return rng.choice(idx, size=k, replace=False)


# --- completeness -----------------------------------------------------------------------


@operator("null_injection", "completeness")
def null_injection(df, col, rng, rate):
    rows = _rows(df, rng, rate, df[col].notna())
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = None
    return out


@operator("row_drop", "completeness", scope="row")
def row_drop(df, col, rng, rate):
    rows = _rows(df, rng, rate)
    return df.drop(index=rows)


@operator("row_duplicate", "uniqueness", scope="row")
def row_duplicate(df, col, rng, rate):
    rows = _rows(df, rng, rate)
    return pd.concat([df, df.loc[rows]], ignore_index=True)


@operator("default_value", "completeness")
def default_value(df, col, rng, rate):
    """A failed lookup falls back to a placeholder (0, 'UNKNOWN', 1900-01-01)."""
    typ = infer_type(df[col])
    placeholder = {"integer": 0, "number": 0.0, "date": "1900-01-01", "boolean": False}.get(typ, "UNKNOWN")
    rows = _rows(df, rng, rate, df[col].astype(str) != str(placeholder))
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = placeholder
    return out


# --- accuracy ---------------------------------------------------------------------------


@operator("numeric_shift", "accuracy", applies_to=("integer", "number"))
def numeric_shift(df, col, rng, rate):
    """Unit or scaling error (cents vs dollars, x100 percent) on a subset of rows."""
    v = pd.to_numeric(df[col], errors="coerce")
    rows = _rows(df, rng, rate, v.notna() & (v != 0))
    if not len(rows):
        return None
    out = df.copy()
    factor = rng.choice([10.0, 100.0, 0.01])
    new = v.astype(float)
    new.loc[rows] = v.loc[rows] * factor
    if infer_type(df[col]) == "integer" and factor >= 1:
        new = new.round().astype("Int64")
    out[col] = new
    return out


@operator("off_by_one", "accuracy", applies_to=("integer", "number"))
def off_by_one(df, col, rng, rate):
    """Boundary / rounding error: value moves by one unit."""
    v = pd.to_numeric(df[col], errors="coerce")
    rows = _rows(df, rng, rate, v.notna())
    if not len(rows):
        return None
    out = df.copy()
    new = v.copy()
    new.loc[rows] = v.loc[rows] + rng.choice([-1, 1], size=len(rows))
    out[col] = new
    return out


@operator("date_shift", "accuracy", applies_to=("date",))
def date_shift(df, col, rng, rate):
    """Timezone/epoch/format bug moves dates (1 day, a month, or a century)."""
    d = pd.to_datetime(df[col], errors="coerce", format="mixed")
    rows = _rows(df, rng, rate, d.notna())
    if not len(rows):
        return None
    out = df.copy()
    shift = rng.choice([1, 31, 36525])
    new = d.copy()
    new.loc[rows] = d.loc[rows] + pd.to_timedelta(int(shift) * rng.choice([-1, 1]), unit="D")
    out[col] = new.dt.strftime("%Y-%m-%d").where(new.notna(), None)
    return out


@operator("date_format", "consistency", applies_to=("date",))
def date_format(df, col, rng, rate):
    """Locale bug: ISO dates rendered as MM/DD/YYYY."""
    d = pd.to_datetime(df[col], errors="coerce", format="mixed")
    rows = _rows(df, rng, rate, d.notna())
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = d.loc[rows].dt.strftime("%m/%d/%Y")
    return out


# --- format / consistency ---------------------------------------------------------------


@operator("truncate", "consistency", applies_to=("string",))
def truncate(df, col, rng, rate):
    """VARCHAR too narrow: strings cut to half their length."""
    s = df[col].astype("string")
    rows = _rows(df, rng, rate, (s.str.len() > 1).fillna(False))
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = [v[: max(1, len(v) // 2)] for v in s.loc[rows]]
    return out


@operator("case_change", "consistency", applies_to=("string",))
def case_change(df, col, rng, rate):
    s = df[col].astype("string")
    rows = _rows(df, rng, rate, s.notna())
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    vals = s.loc[rows]
    out.loc[rows, col] = [v.lower() if v != v.lower() else v.upper() for v in vals]
    return out


@operator("whitespace", "consistency", applies_to=("string",))
def whitespace(df, col, rng, rate):
    """Untrimmed padding from a fixed-width or CHAR source."""
    s = df[col].astype("string")
    rows = _rows(df, rng, rate, s.notna())
    if not len(rows):
        return None
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = [" " + v + "  " for v in s.loc[rows]]
    return out


@operator("type_corruption", "validity")
def type_corruption(df, col, rng, rate):
    """Garbage from an encoding or parsing bug."""
    rows = _rows(df, rng, rate)
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = rng.choice(["#N/A", "��", "NaN?", "--"], size=len(rows))
    return out


@operator("out_of_domain", "validity", applies_to=("string", "integer", "boolean"))
def out_of_domain(df, col, rng, rate):
    """New, unmapped code value (e.g. a code-set change upstream)."""
    rows = _rows(df, rng, rate)
    out = df.copy()
    out[col] = out[col].astype(object)
    out.loc[rows, col] = "ZZ"
    return out


@operator("value_swap", "accuracy")
def value_swap(df, col, rng, rate):
    """Values shuffled between rows: a misaligned join or sort bug."""
    rows = _rows(df, rng, max(rate, 2 / max(len(df), 1)))
    if len(rows) < 2:
        return None
    out = df.copy()
    vals = out.loc[rows, col].to_numpy(copy=True)
    out.loc[rows, col] = np.roll(vals, 1)
    return out


@operator("column_swap", "accuracy", scope="table")
def column_swap(df, col, rng, rate):
    """Two same-typed columns swapped in a SELECT list."""
    typ = infer_type(df[col])
    others = [c for c in df.columns if c != col and infer_type(df[c]) == typ]
    if not others:
        return None
    other = others[int(rng.integers(len(others)))]
    out = df.copy()
    out[col], out[other] = df[other].values, df[col].values
    return out


# --------------------------------------------------------------------------- engine


@dataclass
class Mutant:
    operator: str
    fault_class: str
    column: Optional[str]
    rate: float
    data: pd.DataFrame = field(repr=False)


@dataclass
class MutantResult:
    operator: str
    fault_class: str
    column: Optional[str]
    killed: bool
    killed_by: List[str]

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class MutationReport:
    spec_name: str
    dataset: str
    results: List[MutantResult]
    equivalent: int
    baseline_failing: List[str]
    seconds: float

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def killed(self) -> int:
        return sum(r.killed for r in self.results)

    @property
    def score(self) -> float:
        return self.killed / self.total if self.total else float("nan")

    def by(self, attr: str) -> Dict[str, Dict[str, float]]:
        agg: Dict[str, List[bool]] = defaultdict(list)
        for r in self.results:
            agg[getattr(r, attr)].append(r.killed)
        return {k: {"mutants": len(v), "killed": sum(v), "score": sum(v) / len(v)} for k, v in sorted(agg.items())}

    @property
    def survivors(self) -> List[MutantResult]:
        return [r for r in self.results if not r.killed]

    def to_dict(self):
        return {
            "spec": self.spec_name,
            "dataset": self.dataset,
            "mutants": self.total,
            "killed": self.killed,
            "score": round(self.score, 4) if self.total else None,
            "equivalent_skipped": self.equivalent,
            "baseline_failing_checks": self.baseline_failing,
            "by_operator": self.by("operator"),
            "by_fault_class": self.by("fault_class"),
            "survivors": [r.to_dict() for r in self.survivors],
            "seconds": round(self.seconds, 3),
        }


def generate_mutants(
    df: pd.DataFrame,
    operators: Optional[Iterable[str]] = None,
    columns: Optional[Iterable[str]] = None,
    rate: float = 0.05,
    seed: int = 0,
) -> Iterable[Mutant]:
    """Yield one mutant per (operator, applicable column); row/table-scoped operators once per table."""
    rng = np.random.default_rng(seed)
    ops = [OPERATORS[o] for o in (operators or OPERATORS)]
    # flattener bookkeeping (`_index...`) is not stored in the documents, so it is not mutated by default
    cols = list(columns or [c for c in df.columns if not str(c).startswith("_index")])
    types = {c: infer_type(df[c]) for c in cols}
    for op in ops:
        targets = [None] if op.scope == "row" else [c for c in cols if types[c] in op.applies_to]
        for col in targets:
            mutated = op.fn(df, col if col is not None else cols[0], rng, rate)
            if mutated is None:
                continue
            yield Mutant(op.name, op.fault_class, col, rate, mutated)


def _equivalent(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if a.shape != b.shape:
        return False
    return a.reset_index(drop=True).astype(str).equals(b.reset_index(drop=True).astype(str))


def touched_datasets(check) -> set:
    """Datasets a check reads; used to re-run only affected checks for each mutant."""
    names = {check.dataset}
    for k in ("source", "target", "ref_dataset"):
        if check.params.get(k):
            names.add(check.params[k])
    if isinstance(check.params.get("parent"), dict):
        names.add(check.params["parent"].get("dataset"))
    return {n for n in names if n}


def mutation_test(
    spec: Spec,
    data: Dict[str, pd.DataFrame],
    dataset: str = "target",
    operators: Optional[Iterable[str]] = None,
    columns: Optional[Iterable[str]] = None,
    rate: float = 0.05,
    seed: int = 0,
) -> MutationReport:
    """Run the spec against every mutant of `data[dataset]` and report the mutation score."""
    start = time.perf_counter()
    base_ctx = Context(dict(data))
    baseline = {c.id: run_check(base_ctx, c) for c in spec.checks}
    live = [
        c for c in spec.checks if baseline[c.id].passed and (dataset in touched_datasets(c) or not touched_datasets(c))
    ]
    failing = [cid for cid, r in baseline.items() if not r.passed]
    original = data[dataset]
    results: List[MutantResult] = []
    equivalent = 0
    for m in generate_mutants(original, operators, columns, rate, seed):
        if _equivalent(original, m.data):
            equivalent += 1
            continue
        ctx = Context({**data, dataset: m.data})
        killers = [c.id for c in live if not run_check(ctx, c).passed]
        results.append(MutantResult(m.operator, m.fault_class, m.column, bool(killers), killers))
    return MutationReport(spec.name, dataset, results, equivalent, failing, time.perf_counter() - start)
