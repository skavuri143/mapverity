"""E6: does ERAD-QA find pipeline defects earlier, per unit of test cost, than standard orderings?

    python experiments/erad_qa/e6_prioritization.py [--quick]

Semi-synthetic release study on the member eligibility pipeline (examples/member_eligibility).

* The transformation has 18 code units (functions in transform.py); `ownership.yaml` maps each
  to the document fields it produces. Each unit owns one or two of the 20 realistic defects.
* Each release changes 1-5 code units. For every changed unit a REAL module is drawn from one
  NASA MDP project (rotating per release): its static metrics feed the defect-risk model, and its
  REAL defect label decides whether a defect is injected into that unit in this release. The risk
  model is trained leave-one-project-out, so it never saw the project, the module or its label.
* Each release runs on freshly generated data (different seed). All 49 mapping-derived checks
  (suite S4) run; measured per-check runtimes are the costs. Which check detects which injected
  defect is established exactly by re-running the suite with that defect alone.
* Releases 1-100 are warm-up (history accumulates; ERAD's signal weights and its cost exponent are
learned from these releases only); releases 101-300 are evaluated.

Strategies: random (mean of 20 permutations), cheapest-first, history (decayed failure rate),
change-based, risk-only, ERAD (default weights), ERAD (learned weights), ablations of the learned
model (without history, risk, change, drift, last-failure, cost), and the oracle (upper bound).
Metrics: APFD, APFDc, cost to first failure (share of suite cost), share of injected defects found
within 10% and 25% of suite cost. 95% bootstrap CIs over releases; Wilcoxon signed-rank and
Vargha-Delaney A12 against ERAD (learned).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "member_eligibility"
sys.path.insert(0, str(EX))

import generate_data  # noqa: E402
import transform as T  # noqa: E402

import mapverity  # noqa: E402
from mapverity.ai.datasets import LABEL, PORTABLE_FEATURES, load_nasa_mdp  # noqa: E402
from mapverity.ai.predictor import DefectRiskModel  # noqa: E402
from mapverity.ai.prioritizer import (  # noqa: E402
    SIGNALS,
    ERADPrioritizer,
    PlanSample,
    apfd,
    apfdc,
    cost_to_first_detection,
    detected_within_budget,
    tune_alpha,
)
from mapverity.ai.signals import (  # noqa: E402
    ProfileProbe,
    RunHistory,
    changed_data_units,
    data_unit_risk,
    load_ownership,
)
from mapverity.core.engine import run_check  # noqa: E402
from mapverity.validation.checks import Context  # noqa: E402
from mapverity.validation.documents import flatten_documents  # noqa: E402
from mapverity.validation.mapping import spec_from_mapping  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
UNIT_DEFECTS = {
    "transform:select_members": ["D01_filter_includes_terminated", "D02_filter_drops_pending"],
    "transform:map_last_name": ["D03_name_not_trimmed"],
    "transform:map_first_name": ["D04_name_case_not_normalised"],
    "transform:map_birth_date": ["D05_dob_month_day_swapped"],
    "transform:map_gender": ["D06_gender_unknown_mapped_null"],
    "transform:map_monthly_income": ["D07_comp3_scale_lost"],
    "transform:compute_fpl_percent": ["D08_fpl_uses_annual_income_twice", "D09_fpl_hh_off_by_one"],
    "transform:map_county": ["D10_county_leading_zero_lost"],
    "transform:format_phone": ["D11_phone_format_wrong"],
    "transform:map_status": ["D12_status_lookup_swapped"],
    "transform:build_eligibility": ["D13_segments_inner_join_drops_last"],
    "transform:map_end_date": ["D14_segment_end_date_shifted", "D15_open_end_date_defaulted"],
    "transform:map_plan_code": ["D16_plan_code_untranslated"],
    "transform:map_aid_category": ["D17_aid_category_not_uppercased"],
    "transform:write_documents": ["D18_duplicate_documents"],
    "transform:lineage": ["D19_source_system_wrong"],
    "transform:map_household_size": ["D20_household_size_from_wrong_field"],
    "transform:map_end_date_helpers": [],
}
UNITS = [u for u, d in UNIT_DEFECTS.items() if d]


def run_suite(spec, data):
    ctx = Context(data)
    out = {}
    for c in spec.checks:
        r = run_check(ctx, c)
        out[c.id] = (not r.passed, max(r.seconds, 1e-5))
    return out


def a12(x, y):
    """Vargha-Delaney A12: probability a value from x exceeds one from y (0.5 = no effect)."""
    x, y = np.asarray(x), np.asarray(y)
    gt = (x[:, None] > y[None, :]).sum()
    eq = (x[:, None] == y[None, :]).sum()
    return float((gt + 0.5 * eq) / (len(x) * len(y)))


def boot_ci(x, n=2000, seed=0):
    x = np.asarray([v for v in x if v == v], dtype=float)
    rng = np.random.default_rng(seed)
    means = [rng.choice(x, len(x)).mean() for _ in range(n)]
    return float(x.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--members", type=int, default=1000)
    a = ap.parse_args()
    n_rel, warm = (60, 20) if a.quick else (300, 100)
    rng = np.random.default_rng(2026)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "raw_e6"
    tmp.mkdir(exist_ok=True)
    t0 = time.time()

    ownership = load_ownership(EX / "ownership.yaml")
    spec = spec_from_mapping(EX / "mapping" / "member_data_model_mapping.csv")
    frames = load_nasa_mdp(features=PORTABLE_FEATURES)
    projects = sorted(frames)
    print("training leave-one-project-out risk models...", flush=True)
    models = {}
    for p in projects:
        train = pd.concat([df for n, df in frames.items() if n != p], ignore_index=True)
        models[p] = DefectRiskModel("xgboost", features=PORTABLE_FEATURES, seed=0).fit(
            train[PORTABLE_FEATURES], train[LABEL]
        )

    # each unit starts with a module from a random project (its "current code")
    current = {}
    for u in UNITS:
        p = projects[int(rng.integers(len(projects)))]
        row = frames[p].iloc[int(rng.integers(len(frames[p])))]
        current[u] = (p, row)

    history = RunHistory(keep=500)
    prior = ERADPrioritizer()
    learned = ERADPrioritizer()
    training_rows = []
    alpha_samples = []
    probe = None
    records = []
    check_ids = [c.id for c in spec.checks]

    for rel in range(1, n_rel + 1):
        k = int(min(5, 1 + rng.poisson(1.5)))
        changed_code = list(rng.choice(UNITS, size=k, replace=False))
        proj = projects[int(rng.integers(len(projects)))]
        defects, risk_code = [], {}
        for u in changed_code:
            row = frames[proj].iloc[int(rng.integers(len(frames[proj])))]
            current[u] = (proj, row)
            if int(row[LABEL]) == 1:
                defects.append(str(rng.choice(UNIT_DEFECTS[u])))
        for u, (p, row) in current.items():
            risk_code[u] = float(models[p].predict_proba(row[PORTABLE_FEATURES].to_frame().T.astype(float))[0])
        risk = data_unit_risk(risk_code, ownership)
        changed = changed_data_units(changed_code, ownership)

        land = tmp / f"land_{rel}"
        generate_data.generate(a.members, 1000 + rel, land)
        master, elig = T.read_sources(land)
        src = {"member_master": master, "elig_segments": elig}
        data = {**src, **flatten_documents(T.transform(master, elig, set(defects)), "members", key="memberId")}

        t_probe = time.perf_counter()
        drift = probe.drift(data) if probe else {}
        t_probe = time.perf_counter() - t_probe

        outcome = run_suite(spec, data)
        failing = {c for c, (f, _) in outcome.items() if f}
        cost = {c: s for c, (_, s) in outcome.items()}
        detects = {}
        for d in dict.fromkeys(defects):
            single = {**src, **flatten_documents(T.transform(master, elig, {d}), "members", key="memberId")}
            detects[d] = sorted(c for c, (f, _) in run_suite(spec, single).items() if f)

        if rel <= warm and detects and any(detects.values()):
            alpha_samples.append(
                PlanSample(
                    features={c.id: prior.features(c, history, risk, changed, drift) for c in spec.checks},
                    expected_cost={c.id: prior.cost(c, history) for c in spec.checks},
                    detects=detects,
                    actual_cost=dict(cost),
                )
            )
        if rel > warm and detects and any(detects.values()):
            plans = {}
            t_plan = time.perf_counter()
            plans["ERAD (learned)"] = [p.check for p in learned.plan(spec, history, risk, changed, drift)]
            t_plan = time.perf_counter() - t_plan
            plans["ERAD (default weights)"] = [p.check for p in prior.plan(spec, history, risk, changed, drift)]
            for sig in SIGNALS:
                abl = ERADPrioritizer(
                    weights=learned.weights, alpha=learned.alpha, enabled=tuple(s for s in SIGNALS if s != sig)
                )
                plans[f"ERAD w/o {sig}"] = [p.check for p in abl.plan(spec, history, risk, changed, drift)]
            plans["ERAD w/o cost"] = [
                p.check
                for p in ERADPrioritizer(weights=learned.weights, alpha=0.0).plan(spec, history, risk, changed, drift)
            ]
            exp_cost = {c.id: prior.cost(c, history) for c in spec.checks}
            plans["cheapest-first"] = sorted(check_ids, key=lambda c: exp_cost[c])
            plans["history"] = sorted(check_ids, key=lambda c: (-history.failure_rate(c), exp_cost[c]))
            feat = {c.id: prior.features(c, history, risk, changed, drift) for c in spec.checks}
            plans["change-based"] = sorted(check_ids, key=lambda c: (-feat[c]["change"], exp_cost[c]))
            risk_only = ERADPrioritizer(unchanged_risk_discount=1.0)
            rfeat = {c.id: risk_only.features(c, None, risk, changed, None)["risk"] for c in spec.checks}
            plans["risk-only"] = sorted(check_ids, key=lambda c: (-rfeat[c], exp_cost[c]))
            plans["oracle"] = sorted(check_ids, key=lambda c: (c not in failing, cost[c]))
            total = sum(cost.values())
            for name, order in plans.items():
                records.append(
                    {
                        "release": rel,
                        "strategy": name,
                        "defects": len(detects),
                        "apfd": apfd(order, detects),
                        "apfdc": apfdc(order, detects, cost),
                        "first_failure_cost_share": cost_to_first_detection(order, detects, cost) / total,
                        "found_at_10pct": detected_within_budget(order, detects, cost, 0.10),
                        "found_at_25pct": detected_within_budget(order, detects, cost, 0.25),
                        "overhead_share": (t_probe + t_plan) / total if name == "ERAD (learned)" else np.nan,
                    }
                )
            rnd = defaultdict(list)
            for _ in range(20):
                order = list(rng.permutation(check_ids))
                rnd["apfd"].append(apfd(order, detects))
                rnd["apfdc"].append(apfdc(order, detects, cost))
                rnd["first_failure_cost_share"].append(cost_to_first_detection(order, detects, cost) / total)
                rnd["found_at_10pct"].append(detected_within_budget(order, detects, cost, 0.10))
                rnd["found_at_25pct"].append(detected_within_budget(order, detects, cost, 0.25))
            records.append(
                {
                    "release": rel,
                    "strategy": "random",
                    "defects": len(detects),
                    **{k2: float(np.mean(v)) for k2, v in rnd.items()},
                    "overhead_share": np.nan,
                }
            )

        # learning signal: features BEFORE the run, outcome AFTER it
        if rel <= warm:
            for c in spec.checks:
                training_rows.append((prior.features(c, history, risk, changed, drift), c.id in failing))
        for cid, (f, s) in outcome.items():
            history.record(cid, f, s, ts=float(rel))
        if rel == warm:
            learned.fit_weights(training_rows)
            tune_alpha(learned, alpha_samples)
            print(f"learned weights after warm-up: {learned.weights}, alpha={learned.alpha}", flush=True)
        if probe is None and not defects:
            probe = ProfileProbe.from_data(data, tables=["members", "members.eligibility"])
        if rel % 25 == 0:
            print(f"release {rel}/{n_rel} ({time.time() - t0:.0f}s)", flush=True)
        for f in land.iterdir():
            f.unlink()
        land.rmdir()

    df = pd.DataFrame(records)
    df.to_csv(OUT / "e6_releases.csv", index=False)
    metrics = ["apfd", "apfdc", "first_failure_cost_share", "found_at_10pct", "found_at_25pct"]
    summary = {
        "meta": {
            "mapverity": mapverity.__version__,
            "python": platform.python_version(),
            "releases": n_rel,
            "warm_up": warm,
            "members_per_release": a.members,
            "evaluated_releases_with_detectable_defects": int(df["release"].nunique()),
            "checks": len(spec.checks),
            "learned_weights": learned.weights.__dict__,
            "learned_alpha": learned.alpha,
            "alpha_tuning_releases": len(alpha_samples),
            "wall_clock_s": round(time.time() - t0, 1),
        },
        "strategies": {},
    }
    ref = df[df.strategy == "ERAD (learned)"].set_index("release")
    for name, g in df.groupby("strategy"):
        g = g.set_index("release").loc[ref.index]
        entry = {m: dict(zip(("mean", "ci_low", "ci_high"), boot_ci(g[m]))) for m in metrics}
        if name != "ERAD (learned)":
            for m in ("apfdc", "first_failure_cost_share"):
                x, y = ref[m].to_numpy(), g[m].to_numpy()
                ok = ~(np.isnan(x) | np.isnan(y))
                diff = x[ok] - y[ok]
                p = float(stats.wilcoxon(x[ok], y[ok]).pvalue) if np.any(diff != 0) else 1.0
                entry[f"vs_erad_{m}"] = {"wilcoxon_p": p, "a12_erad_greater": a12(x[ok], y[ok])}
        summary["strategies"][name] = entry
    ov = ref["overhead_share"].dropna()
    summary["erad_overhead_share_of_suite_cost"] = {"median": float(ov.median()), "p90": float(ov.quantile(0.9))}
    (OUT / "e6_summary.json").write_text(json.dumps(summary, indent=2))
    table = pd.DataFrame({k: {m: v[m]["mean"] for m in metrics} for k, v in summary["strategies"].items()}).T
    print(table.sort_values("apfdc", ascending=False).round(3).to_string())


if __name__ == "__main__":
    main()
