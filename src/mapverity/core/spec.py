"""Spec model: a declarative description of what a correct pipeline output looks like."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

_CORE_KEYS = {"type", "dataset", "column", "name", "severity"}


@dataclass
class DatasetSpec:
    """Where a dataset lives. Datasets can also be passed in directly as DataFrames."""

    path: Optional[str] = None
    format: Optional[str] = None  # csv | parquet | json; inferred from extension when omitted
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.path:
            d["path"] = self.path
        if self.format:
            d["format"] = self.format
        if self.options:
            d["options"] = self.options
        return d


@dataclass
class CheckSpec:
    """One check. Everything other than the core keys is a check-specific parameter."""

    type: str
    dataset: Optional[str] = None
    column: Optional[str] = None
    name: Optional[str] = None
    severity: str = "error"  # error | warn
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        if self.name:
            return self.name
        parts = [self.type]
        if self.dataset:
            parts.append(self.dataset)
        if self.column:
            parts.append(self.column)
        return ":".join(parts)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CheckSpec":
        if "type" not in d:
            raise ValueError(f"check is missing 'type': {d!r}")
        params = {k: v for k, v in d.items() if k not in _CORE_KEYS}
        return cls(
            type=d["type"],
            dataset=d.get("dataset"),
            column=d.get("column"),
            name=d.get("name"),
            severity=d.get("severity", "error"),
            params=params,
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.type}
        if self.name:
            d["name"] = self.name
        if self.dataset:
            d["dataset"] = self.dataset
        if self.column:
            d["column"] = self.column
        if self.severity != "error":
            d["severity"] = self.severity
        d.update(self.params)
        return d


@dataclass
class Spec:
    name: str = "pipeline"
    datasets: Dict[str, DatasetSpec] = field(default_factory=dict)
    checks: List[CheckSpec] = field(default_factory=list)
    description: Optional[str] = None
    base_dir: Optional[Path] = None  # paths in `datasets` are resolved against this

    @classmethod
    def from_dict(cls, d: Dict[str, Any], base_dir: Optional[Path] = None) -> "Spec":
        datasets = {}
        for name, ds in (d.get("datasets") or {}).items():
            if isinstance(ds, str):
                ds = {"path": ds}
            datasets[name] = DatasetSpec(path=ds.get("path"), format=ds.get("format"), options=ds.get("options") or {})
        checks = [CheckSpec.from_dict(c) for c in (d.get("checks") or [])]
        return cls(
            name=d.get("name", "pipeline"),
            datasets=datasets,
            checks=checks,
            description=d.get("description"),
            base_dir=base_dir,
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Spec":
        path = Path(path)
        with open(path, encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        return cls.from_dict(d, base_dir=path.parent)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"version": 1, "name": self.name}
        if self.description:
            d["description"] = self.description
        if self.datasets:
            d["datasets"] = {k: v.to_dict() for k, v in self.datasets.items()}
        d["checks"] = [c.to_dict() for c in self.checks]
        return d

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False, width=100)

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(self.to_yaml(), encoding="utf-8")

    def subset(self, predicate) -> "Spec":
        """Return a copy keeping only checks for which predicate(check) is true."""
        s = copy.deepcopy(self)
        s.checks = [c for c in s.checks if predicate(c)]
        return s
