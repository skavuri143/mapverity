"""Run a spec against data and collect results."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import pandas as pd

from ..connectors import read_any
from ..validation.checks import REGISTRY, CheckOutcome, Context
from .spec import CheckSpec, DatasetSpec, Spec


@dataclass
class CheckResult:
    check: CheckSpec
    status: str  # passed | failed | error
    outcome: CheckOutcome = field(default_factory=CheckOutcome)
    error: Optional[str] = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.check.id,
            "type": self.check.type,
            "dataset": self.check.dataset,
            "column": self.check.column,
            "severity": self.check.severity,
            "status": self.status,
            "failed": self.outcome.failed,
            "total": self.outcome.total,
            "message": self.error or self.outcome.message,
            "sample": self.outcome.sample,
            "seconds": round(self.seconds, 6),
        }


@dataclass
class ValidationResult:
    spec_name: str
    results: List[CheckResult]
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return all(r.passed or r.check.severity == "warn" for r in self.results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> Dict[str, int]:
        out = {"total": len(self.results), "passed": 0, "failed": 0, "error": 0}
        for r in self.results:
            out[r.status] += 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec_name,
            "passed": self.passed,
            "summary": self.summary(),
            "seconds": round(self.seconds, 6),
            "results": [r.to_dict() for r in self.results],
        }


_PATH_OPTIONS = ("copybook", "tls_ca_file", "layout")


def _resolve(location: str, base_dir: Optional[Path]) -> str:
    location = os.path.expandvars(location)
    if "://" in location or base_dir is None or Path(location).is_absolute():
        return location
    return str(base_dir / location)


def read_dataset(name: str, ds: DatasetSpec, base_dir: Optional[Path] = None) -> Dict[str, pd.DataFrame]:
    """Read one dataset (a document collection may expand into several tables)."""
    if not ds.path:
        raise ValueError(f"dataset '{name}' has no path; pass it as a DataFrame instead")
    options = dict(ds.options)
    for opt in _PATH_OPTIONS:
        if opt in options:
            options[opt] = _resolve(str(options[opt]), base_dir)
    return read_any(name, _resolve(ds.path, base_dir), ds.format, options)


def load_data(
    spec: Spec, data: Optional[Mapping[str, Union[pd.DataFrame, str, Path]]] = None
) -> Dict[str, pd.DataFrame]:
    """Resolve every dataset: explicit DataFrames/paths in `data` win over paths in the spec."""
    data = dict(data or {})
    out: Dict[str, pd.DataFrame] = {}
    for name, ds in spec.datasets.items():
        if name not in data and ds.path:
            out.update(read_dataset(name, ds, spec.base_dir))
    for name, value in data.items():
        if isinstance(value, pd.DataFrame):
            out[name] = value
        else:
            ds = spec.datasets.get(name, DatasetSpec())
            out.update(
                read_dataset(
                    name,
                    DatasetSpec(path=str(value), format=ds.format, options=ds.options),
                    spec.base_dir if name in spec.datasets else None,
                )
            )
    return out


def run_check(ctx: Context, check: CheckSpec) -> CheckResult:
    fn = REGISTRY.get(check.type)
    start = time.perf_counter()
    if fn is None:
        return CheckResult(check, "error", error=f"unknown check type '{check.type}'")
    try:
        outcome = fn(ctx, check)
        status = "passed" if outcome.ok else "failed"
        return CheckResult(check, status, outcome, seconds=time.perf_counter() - start)
    except Exception as exc:  # a broken check must not hide other results
        return CheckResult(check, "error", error=f"{type(exc).__name__}: {exc}", seconds=time.perf_counter() - start)


def validate(spec: Union[Spec, str, Path], data: Optional[Mapping[str, Any]] = None) -> ValidationResult:
    """Validate data against a spec.

    >>> result = validate("pipeline.yaml", {"target": df})
    >>> result.passed
    """
    if not isinstance(spec, Spec):
        spec = Spec.load(spec)
    start = time.perf_counter()
    ctx = Context(load_data(spec, data))
    results = [run_check(ctx, c) for c in spec.checks]
    return ValidationResult(spec.name, results, seconds=time.perf_counter() - start)
