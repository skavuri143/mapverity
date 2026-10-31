"""Signals for risk-adaptive prioritization.

* RunHistory    per-check outcomes and durations across runs (JSON file, safe to commit or cache in CI)
* ProfileProbe  cheap per-column profiles of target tables; drift vs. a baseline is an early,
                observed data-integrity signal computed before any expensive check runs
* change impact which fields/units changed: `diff_mappings` compares two mapping sheets
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import numpy as np
import pandas as pd

from ..validation.mapping import FieldMapping, load_mapping

# --------------------------------------------------------------------------- history


@dataclass
class RunHistory:
    """Outcomes per check id: list of [timestamp, failed(0/1), seconds]. Bounded to `keep` runs."""

    runs: Dict[str, List[List[float]]] = field(default_factory=dict)
    keep: int = 200

    @classmethod
    def load(cls, path: Union[str, Path]) -> "RunHistory":
        p = Path(path)
        if not p.exists():
            return cls()
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(runs=d.get("runs", {}), keep=d.get("keep", 200))

    def save(self, path: Union[str, Path]) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"keep": self.keep, "runs": self.runs}), encoding="utf-8")

    def record(self, check_id: str, failed: bool, seconds: float, ts: Optional[float] = None) -> None:
        rows = self.runs.setdefault(check_id, [])
        rows.append([ts if ts is not None else time.time(), 1.0 if failed else 0.0, float(seconds)])
        del rows[: max(0, len(rows) - self.keep)]

    def record_result(self, result: Any) -> None:
        now = time.time()
        for r in result.results:
            self.record(r.check.id, not r.passed, r.seconds, now)

    def failure_rate(
        self, check_id: str, prior_fail: float = 0.5, prior_pass: float = 4.5, decay: float = 0.97
    ) -> float:
        """Exponentially-decayed Beta posterior mean: recent failures weigh more."""
        rows = self.runs.get(check_id, [])
        if not rows:
            return prior_fail / (prior_fail + prior_pass)
        w = decay ** np.arange(len(rows))[::-1]
        fails = float((w * np.array([r[1] for r in rows])).sum())
        total = float(w.sum())
        return (prior_fail + fails) / (prior_fail + prior_pass + total)

    def last_failed(self, check_id: str) -> bool:
        rows = self.runs.get(check_id)
        return bool(rows and rows[-1][1] > 0)

    def median_seconds(self, check_id: str) -> Optional[float]:
        rows = self.runs.get(check_id)
        return float(np.median([r[2] for r in rows])) if rows else None


# --------------------------------------------------------------------------- profile drift


def _shape(v: str) -> str:
    out = []
    for ch in v[:24]:
        out.append("9" if ch.isdigit() else "A" if ch.isupper() else "a" if ch.islower() else ch)
    return "".join(out)


def profile_column(s: pd.Series) -> Dict[str, Any]:
    nn = s.dropna()
    prof: Dict[str, Any] = {
        "null_rate": float(s.isna().mean()) if len(s) else 0.0,
        "distinct_ratio": float(nn.nunique() / len(nn)) if len(nn) else 0.0,
    }
    num = pd.to_numeric(nn, errors="coerce")
    is_text = nn.map(lambda v: isinstance(v, str)).mean() if len(nn) else 0
    if len(nn) and num.notna().mean() > 0.95 and is_text < 0.5:
        prof["mean"] = float(num.mean())
        prof["std"] = float(num.std() or 0.0)
    else:
        shapes = nn.astype(str).map(_shape).value_counts(normalize=True).head(20)
        prof["shapes"] = {k: float(v) for k, v in shapes.items()}
        prof["mean_len"] = float(nn.astype(str).str.len().mean()) if len(nn) else 0.0
    return prof


def profile_table(df: pd.DataFrame, sample: int = 2000, seed: int = 0) -> Dict[str, Dict[str, Any]]:
    d = df.sample(n=min(sample, len(df)), random_state=seed) if len(df) > sample else df
    return {str(c): profile_column(d[c]) for c in d.columns}


def column_drift(base: Dict[str, Any], cur: Dict[str, Any]) -> float:
    """0 = same profile, 1 = very different. Max over null-rate, distinct-ratio, mean shift, shape TVD."""
    parts = [abs(cur["null_rate"] - base["null_rate"]) * 4, abs(cur["distinct_ratio"] - base["distinct_ratio"]) * 2]
    if "mean" in base and "mean" in cur:
        scale = max(base.get("std") or 0.0, abs(base["mean"]) * 0.05, 1e-9)
        parts.append(min(abs(cur["mean"] - base["mean"]) / scale / 3, 1.0))
    elif "shapes" in base and "shapes" in cur:
        keys = set(base["shapes"]) | set(cur["shapes"])
        tvd = 0.5 * sum(abs(base["shapes"].get(k, 0) - cur["shapes"].get(k, 0)) for k in keys)
        parts.append(tvd)
        parts.append(min(abs(cur["mean_len"] - base["mean_len"]) / max(base["mean_len"], 1) * 2, 1.0))
    elif ("mean" in base) != ("mean" in cur):
        parts.append(1.0)  # type changed
    return float(min(max(parts), 1.0))


@dataclass
class ProfileProbe:
    baseline: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    sample: int = 2000

    @classmethod
    def from_data(
        cls, data: Dict[str, pd.DataFrame], tables: Optional[List[str]] = None, sample: int = 2000
    ) -> "ProfileProbe":
        return cls({t: profile_table(df, sample) for t, df in data.items() if tables is None or t in tables}, sample)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "ProfileProbe":
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else cls()

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(json.dumps(self.baseline), encoding="utf-8")

    def drift(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """{'table.column': drift} for every baselined column present in `data`."""
        out: Dict[str, float] = {}
        for table, cols in self.baseline.items():
            df = data.get(table)
            if df is None:
                continue
            cur = profile_table(df, self.sample)
            for c, base in cols.items():
                out[f"{table}.{c}"] = 1.0 if c not in cur else column_drift(base, cur[c])
            for c in cur:
                if c not in cols:
                    out[f"{table}.{c}"] = 1.0
        return out


# --------------------------------------------------------------------------- change impact


def _rule_sig(m: FieldMapping) -> tuple:
    return (
        m.source_dataset,
        m.source_field,
        m.rule,
        m.data_type,
        m.required,
        m.key,
        tuple(m.allowed_values),
        m.source_filter,
        m.tolerance,
    )


def diff_mappings(
    old: Union[str, Path, List[FieldMapping]], new: Union[str, Path, List[FieldMapping]]
) -> Dict[str, List[str]]:
    """Fields added, removed or changed between two versions of a mapping sheet."""
    a = {
        f"{m.target_table}.{m.target_field}": _rule_sig(m)
        for m in (old if isinstance(old, list) else load_mapping(old))
    }
    b = {
        f"{m.target_table}.{m.target_field}": _rule_sig(m)
        for m in (new if isinstance(new, list) else load_mapping(new))
    }
    return {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
    }


def changed_units(diff: Dict[str, List[str]]) -> Set[str]:
    return set(diff.get("added", [])) | set(diff.get("changed", []))


# --------------------------------------------------------------------------- code -> data ownership


def load_ownership(path: Union[str, Path]) -> Dict[str, List[str]]:
    """YAML map of code units to the data units they produce, e.g.

    transform:map_gender: [members.gender]
    transform:build_eligibility: [members.eligibility.*, members.eligibility._count]
    """
    import yaml

    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in d.items()}


def data_unit_risk(code_risk: Dict[str, float], ownership: Dict[str, List[str]]) -> Dict[str, float]:
    """Propagate code-unit risk to the data units each code unit produces (max over producers)."""
    out: Dict[str, float] = {}
    for code_unit, p in code_risk.items():
        for data_unit in ownership.get(code_unit, []):
            out[data_unit] = max(out.get(data_unit, 0.0), float(p))
    return out


def changed_data_units(changed_code: List[str], ownership: Dict[str, List[str]]) -> Set[str]:
    out: Set[str] = set()
    for u in changed_code:
        out.update(ownership.get(u, []))
    return out
