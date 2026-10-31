"""Deterministic quality decisions for CI/CD.

A release decision is computed from evidence: validation results, mapping coverage, mutation
score and (optionally) predicted risk. The rules are fixed and auditable:

* any failed or errored check with severity `error` BLOCKS: proven integrity failures are never
  outweighed by a probabilistic score;
* checks that were not run (a time budget ran out) make the decision INCOMPLETE, never PASS;
* thresholds on coverage and mutation score BLOCK or WARN as the policy says;
* predicted risk is ADVISORY (WARN) unless the policy explicitly sets `risk_blocks: true`.

    policy = GatePolicy.load("quality-gates.yaml")
    decision = evaluate_gates(policy, result=validation_result, coverage=cov, mutation=mut, risk=scores)
    sys.exit(decision.exit_code)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import yaml

PASS, WARN, BLOCK, INCOMPLETE = "PASS", "WARN", "BLOCK", "INCOMPLETE"
_RANK = {PASS: 0, WARN: 1, INCOMPLETE: 2, BLOCK: 3}


@dataclass
class GatePolicy:
    min_value_coverage: Optional[float] = None  # share of mapped fields value-validated
    coverage_action: str = BLOCK  # BLOCK | WARN
    min_mutation_score: Optional[float] = None
    mutation_action: str = BLOCK
    max_warn_failures: Optional[int] = None  # failed severity=warn checks tolerated
    risk_warn_threshold: Optional[float] = 0.7  # predicted risk at/above this → WARN
    risk_blocks: bool = False  # opt-in only: risk alone never blocks by default
    require_all_blocking_checks: bool = True  # unrun blocking checks → INCOMPLETE

    @classmethod
    def load(cls, path: Union[str, Path]) -> "GatePolicy":
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        d = d.get("gates", d) or {}
        if not isinstance(d, dict):
            raise ValueError("gate policy must be a mapping of settings")
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown gate settings: {sorted(unknown)} (known: {sorted(known)})")
        return cls(**d)


@dataclass
class GateFinding:
    gate: str
    outcome: str
    detail: str


@dataclass
class Decision:
    outcome: str
    findings: List[GateFinding] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome in (PASS, WARN) else 1

    def to_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "exit_code": self.exit_code, "findings": [asdict(f) for f in self.findings]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def summary(self) -> str:
        lines = [f"Quality gate: {self.outcome}"]
        for f in self.findings:
            lines.append(f"  [{f.outcome}] {f.gate}: {f.detail}")
        return "\n".join(lines)


def evaluate_gates(
    policy: GatePolicy,
    result: Optional[Any] = None,
    coverage: Optional[Mapping[str, Any]] = None,
    mutation: Optional[Mapping[str, Any]] = None,
    risk: Optional[Mapping[str, float]] = None,
    not_run: Optional[List[str]] = None,
) -> Decision:
    findings: List[GateFinding] = []

    if result is not None:
        blocking = [r for r in result.results if not r.passed and r.check.severity != "warn"]
        warned = [r for r in result.results if not r.passed and r.check.severity == "warn"]
        if blocking:
            ids = ", ".join(r.check.id for r in blocking[:5]) + (" ..." if len(blocking) > 5 else "")
            findings.append(GateFinding("integrity", BLOCK, f"{len(blocking)} blocking check(s) failed: {ids}"))
        else:
            findings.append(GateFinding("integrity", PASS, f"{len(result.results)} check(s) run, no blocking failure"))
        if warned:
            limit = policy.max_warn_failures
            outcome = BLOCK if (limit is not None and len(warned) > limit) else WARN
            findings.append(GateFinding("warnings", outcome, f"{len(warned)} warning-level check(s) failed"))

    if not_run:
        outcome = INCOMPLETE if policy.require_all_blocking_checks else WARN
        findings.append(GateFinding("completeness", outcome, f"{len(not_run)} blocking check(s) not run (budget)"))

    if policy.min_value_coverage is not None:
        if coverage is None:
            findings.append(GateFinding("coverage", INCOMPLETE, "coverage threshold set but no coverage report given"))
        else:
            v = float(coverage.get("value_coverage", 0.0))
            ok = v >= policy.min_value_coverage
            findings.append(
                GateFinding(
                    "coverage",
                    PASS if ok else policy.coverage_action,
                    f"value coverage {v:.1%} (min {policy.min_value_coverage:.1%})",
                )
            )

    if policy.min_mutation_score is not None:
        if mutation is None:
            findings.append(GateFinding("mutation", INCOMPLETE, "mutation threshold set but no mutation report given"))
        else:
            s = float(mutation.get("score") or 0.0)
            ok = s >= policy.min_mutation_score
            findings.append(
                GateFinding(
                    "mutation",
                    PASS if ok else policy.mutation_action,
                    f"mutation score {s:.1%} (min {policy.min_mutation_score:.1%})",
                )
            )

    if risk and policy.risk_warn_threshold is not None:
        high = sorted(((u, p) for u, p in risk.items() if p >= policy.risk_warn_threshold), key=lambda x: -x[1])
        if high:
            outcome = BLOCK if policy.risk_blocks else WARN
            top = ", ".join(f"{u} ({p:.2f})" for u, p in high[:5])
            findings.append(
                GateFinding(
                    "predicted-risk",
                    outcome,
                    f"{len(high)} unit(s) at or above risk {policy.risk_warn_threshold}: {top}"
                    + ("" if policy.risk_blocks else " [advisory]"),
                )
            )

    outcome = max((f.outcome for f in findings), key=lambda o: _RANK[o], default=PASS)
    return Decision(outcome, findings)
