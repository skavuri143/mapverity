"""ERAD-QA: Explainable Risk-Adaptive Data Quality Assurance.

Orders validation checks so the ones most likely to fail run first, per unit of execution cost.
Every check's priority is an additive score whose terms are reported as its explanation:

    priority(c) = ( w_hist  * h(c)          # decayed historical failure rate of the check
                  + w_risk  * r(c)          # max predicted defect risk of the units the check covers
                  + w_change* chg(c)        # 1 if a covered unit changed in this release
                  + w_drift * d(c)          # observed profile drift of the covered columns
                  + w_last  * last(c)       # the check failed on the previous run
                  ) / cost(c) ** alpha

r(c) is discounted by `unchanged_risk_discount` for units that did not change. Weights can be set
in policy or learned from history with `fit_weights` (logistic regression on past runs). Ordering
only changes WHEN a check runs: blocking checks are never skipped silently (see core/gates.py),
and a run cut short by a time budget is INCOMPLETE, never PASS.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from ..core.engine import CheckResult, ValidationResult, load_data, run_check
from ..core.spec import CheckSpec, Spec
from ..validation.checks import Context

SIGNALS = ("hist", "risk", "change", "drift", "last")
DEFAULT_COST = {
    "schema": 0.2,
    "not_null": 0.1,
    "unique": 0.3,
    "allowed_values": 0.2,
    "regex": 0.4,
    "in_range": 0.3,
    "null_fraction": 0.1,
    "mean_within": 0.1,
    "row_rule": 0.6,
    "row_count_match": 0.3,
    "key_match": 0.8,
    "referential": 0.8,
    "mapping": 1.5,
    "group_aggregate": 1.2,
    "aggregate_match": 0.6,
}


def units_of(check: CheckSpec) -> List[str]:
    """Data units ('table.field') a check covers; `units:` in the check overrides."""
    if check.params.get("units"):
        return list(check.params["units"])
    table = check.params.get("target", check.dataset) or check.dataset or ""
    cols: List[str] = []
    if check.column:
        cols.append(check.column)
    if isinstance(check.params.get("columns"), (dict, list)):
        cols += list(check.params["columns"])
    for k in ("key", "target_key"):
        v = check.params.get(k)
        if isinstance(v, str) and check.type in ("key_match", "row_count_match"):
            cols.append(v)
        elif isinstance(v, list) and check.type in ("key_match", "row_count_match"):
            cols += [x for x in v if isinstance(x, str)]
    if not cols:
        return [f"{table}.*"]
    return [f"{table}.{c}" for c in dict.fromkeys(cols)]


@dataclass
class ERADWeights:
    hist: float = 1.0
    risk: float = 1.0
    change: float = 1.0
    drift: float = 2.0
    last: float = 1.0
    intercept: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {k: getattr(self, k) for k in SIGNALS}


@dataclass
class PlanItem:
    check: str
    priority: float
    score: float
    cost: float
    features: Dict[str, float]
    contributions: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ERADPrioritizer:
    weights: ERADWeights = field(default_factory=ERADWeights)
    alpha: float = 1.0  # cost exponent: 0 ignores cost, 1 = score per second
    unchanged_risk_discount: float = 0.25
    enabled: Tuple[str, ...] = SIGNALS  # for ablations

    def features(
        self,
        check: CheckSpec,
        history: Optional[Any] = None,
        risk: Optional[Mapping[str, float]] = None,
        changed: Optional[Set[str]] = None,
        drift: Optional[Mapping[str, float]] = None,
    ) -> Dict[str, float]:
        units = units_of(check)
        changed = changed or set()
        is_changed = any(u in changed or u.rsplit(".", 1)[0] + ".*" in changed for u in units)
        r = 0.0
        if risk:
            vals = [risk.get(u, 0.0) for u in units]
            r = max(vals) if vals else 0.0
            if not is_changed:
                r *= self.unchanged_risk_discount
        d = max((drift or {}).get(u, 0.0) for u in units) if drift else 0.0
        return {
            "hist": history.failure_rate(check.id) if history is not None else 0.1,
            "risk": float(r),
            "change": 1.0 if is_changed else 0.0,
            "drift": float(d),
            "last": 1.0 if (history is not None and history.last_failed(check.id)) else 0.0,
        }

    def cost(self, check: CheckSpec, history: Optional[Any] = None) -> float:
        med = history.median_seconds(check.id) if history is not None else None
        return max(float(med if med is not None else DEFAULT_COST.get(check.type, 0.5)), 1e-4)

    def plan(
        self,
        spec: Spec,
        history: Optional[Any] = None,
        risk: Optional[Mapping[str, float]] = None,
        changed: Optional[Set[str]] = None,
        drift: Optional[Mapping[str, float]] = None,
    ) -> List[PlanItem]:
        w = self.weights.as_dict()
        items = []
        for c in spec.checks:
            f = self.features(c, history, risk, changed, drift)
            contrib = {k: (w[k] * f[k] if k in self.enabled else 0.0) for k in SIGNALS}
            score = sum(contrib.values())
            cost = self.cost(c, history)
            prio = (score + 1e-6) / (cost**self.alpha)
            items.append(PlanItem(c.id, prio, score, cost, f, contrib))
        items.sort(key=lambda p: (-p.priority, p.cost, p.check))
        return items

    def fit_weights(self, rows: Sequence[Tuple[Mapping[str, float], bool]], C: float = 1.0) -> ERADWeights:
        """Learn non-negative signal weights from (features, failed) pairs of past runs."""
        from sklearn.linear_model import LogisticRegression

        if len(rows) < 20 or len({bool(f) for _, f in rows}) < 2:
            return self.weights
        X = np.array([[r[k] for k in SIGNALS] for r, _ in rows])
        y = np.array([1 if f else 0 for _, f in rows])
        lr = LogisticRegression(C=C, class_weight="balanced", max_iter=2000).fit(X, y)
        coef = np.clip(lr.coef_[0], 0, None)
        if coef.sum() == 0:
            return self.weights
        coef = coef / coef.sum() * len(SIGNALS)
        self.weights = ERADWeights(**{k: float(v) for k, v in zip(SIGNALS, coef)}, intercept=float(lr.intercept_[0]))
        return self.weights


@dataclass
class PlanSample:
    """What the prioritizer knew before a past run (features, expected costs) and what that run
    revealed (which checks detect which fault, actual costs). Used to tune `alpha` on history."""

    features: Dict[str, Dict[str, float]]
    expected_cost: Dict[str, float]
    detects: Dict[str, List[str]]
    actual_cost: Dict[str, float]


def order_from_features(prio: "ERADPrioritizer", sample: PlanSample) -> List[str]:
    w = prio.weights.as_dict()
    scored = []
    for cid, f in sample.features.items():
        score = sum(w[k] * f[k] for k in SIGNALS if k in prio.enabled)
        scored.append(((score + 1e-6) / (sample.expected_cost[cid] ** prio.alpha), -sample.expected_cost[cid], cid))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [c for _, _, c in scored]


def tune_alpha(
    prio: "ERADPrioritizer", samples: Sequence[PlanSample], candidates: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0)
) -> float:
    """Pick the cost exponent with the best mean APFDc on past runs; sets and returns it."""
    usable = [s for s in samples if any(s.detects.values())]
    if not usable:
        return prio.alpha
    best, best_score = prio.alpha, -1.0
    for a in candidates:
        prio.alpha = a
        vals = [apfdc(order_from_features(prio, s), s.detects, s.actual_cost) for s in usable]
        score = float(np.nanmean(vals))
        if score > best_score + 1e-9:
            best, best_score = a, score
    prio.alpha = best
    return best


def run_plan(
    spec: Spec,
    plan: Sequence[PlanItem],
    data: Optional[Dict[str, Any]] = None,
    budget_seconds: Optional[float] = None,
    stop_on_failure: bool = False,
) -> Tuple[ValidationResult, List[str]]:
    """Execute checks in plan order. Returns the result and the ids of checks not run."""
    by_id = {c.id: c for c in spec.checks}
    ctx = Context(load_data(spec, data))
    start = time.perf_counter()
    results: List[CheckResult] = []
    not_run: List[str] = []
    stopped = False
    for item in plan:
        check = by_id[item.check]
        if stopped or (budget_seconds is not None and time.perf_counter() - start >= budget_seconds):
            not_run.append(check.id)
            continue
        r = run_check(ctx, check)
        results.append(r)
        if stop_on_failure and not r.passed and check.severity != "warn":
            stopped = True
    return ValidationResult(spec.name, results, time.perf_counter() - start), not_run


# --------------------------------------------------------------------------- evaluation metrics


def apfd(order: Sequence[str], detects: Mapping[str, Iterable[str]]) -> float:
    """Average Percentage of Faults Detected. `detects`: fault -> checks that detect it."""
    n, faults = len(order), [f for f, ts in detects.items() if set(ts) & set(order)]
    if not faults or n == 0:
        return float("nan")
    pos = {t: i + 1 for i, t in enumerate(order)}
    tf = [min(pos[t] for t in detects[f] if t in pos) for f in faults]
    return 1 - sum(tf) / (n * len(faults)) + 1 / (2 * n)


def apfdc(
    order: Sequence[str],
    detects: Mapping[str, Iterable[str]],
    cost: Mapping[str, float],
    severity: Optional[Mapping[str, float]] = None,
) -> float:
    """Cost-cognizant APFD (Elbaum, Malishevsky & Rothermel 2001)."""
    faults = [f for f, ts in detects.items() if set(ts) & set(order)]
    if not faults:
        return float("nan")
    sev = {f: (severity or {}).get(f, 1.0) for f in faults}
    costs = [cost[t] for t in order]
    total = sum(costs)
    pos = {t: i for i, t in enumerate(order)}
    num = 0.0
    for f in faults:
        i = min(pos[t] for t in detects[f] if t in pos)
        num += sev[f] * (sum(costs[i:]) - 0.5 * costs[i])
    return num / (total * sum(sev.values()))


def cost_to_first_detection(
    order: Sequence[str], detects: Mapping[str, Iterable[str]], cost: Mapping[str, float]
) -> float:
    failing = set().union(*[set(v) for v in detects.values()]) if detects else set()
    spent = 0.0
    for t in order:
        spent += cost[t]
        if t in failing:
            return spent
    return float("nan")


def detected_within_budget(
    order: Sequence[str], detects: Mapping[str, Iterable[str]], cost: Mapping[str, float], fraction: float
) -> float:
    faults = [f for f, ts in detects.items() if ts]
    if not faults:
        return float("nan")
    budget = fraction * sum(cost[t] for t in order)
    spent, ran = 0.0, set()
    for t in order:
        if spent + cost[t] > budget:
            break
        spent += cost[t]
        ran.add(t)
    return sum(1 for f in faults if set(detects[f]) & ran) / len(faults)
