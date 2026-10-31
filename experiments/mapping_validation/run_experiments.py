"""Reproduce every number in the mapverity paper.

    python experiments/run_experiments.py            # full run (writes experiments/results/)
    python experiments/run_experiments.py --quick    # smaller, for a smoke test

Design
------
Pipeline: the synthetic member eligibility pipeline in examples/member_eligibility (EBCDIC COBOL member
master + pipe-delimited eligibility segments -> member JSON documents with a nested eligibility
array). Test suites compared:

  S1 structural     types, required fields, key uniqueness (from the data model)
  S2 constraints    S1 + allowed-value domains (from the data model)
  S3 profiled       inferred by mapverity from ONE known-good run (no mapping knowledge)
  S4 mapping        compiled from the source-to-target mapping sheet
  S5 mapping+prof   S4 + S3

S3 is inferred from a "training" run (seed 7) and then applied to independent later runs,
mirroring how inferred tests are used in practice. All other suites need no training data.

E1 seeded defects   20 realistic Glue-job defects x 5 independent runs
E2 mutation score   15 operators on both document tables, 2 fault rates, 3 seeds
E3 false positives  suites run on 20 independent clean runs
E4 scalability      generation-independent timing of decode, flatten and validation
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

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "member_eligibility"
sys.path.insert(0, str(EX))

import generate_data  # noqa: E402
import transform as T  # noqa: E402

import mapverity  # noqa: E402
from mapverity import Spec, infer_spec, mutation_test, validate  # noqa: E402
from mapverity.validation.documents import flatten_documents  # noqa: E402
from mapverity.validation.mapping import load_mapping, mapping_coverage, spec_from_mapping  # noqa: E402

TABLES = ("members", "members.eligibility")
OUT = Path(__file__).resolve().parent / "results"


def run_pipeline(n, seed, tmp: Path, defects=frozenset()):
    land = tmp / f"land_{n}_{seed}"
    if not (land / "MEMBER.MASTER.dat").exists():
        generate_data.generate(n, seed, land)
    master, elig = T.read_sources(land)
    docs = T.transform(master, elig, set(defects))
    tables = flatten_documents(docs, "members", key="memberId")
    return {"member_master": master, "elig_segments": elig, **tables}


def build_suites(train_data):
    mappings = load_mapping(EX / "mapping" / "member_data_model_mapping.csv")
    mapping_spec = spec_from_mapping(mappings, name="S4")
    structural = mapping_spec.subset(lambda c: c.type in ("schema", "not_null", "unique"))
    constraints = mapping_spec.subset(lambda c: (c.name or "").startswith("model:"))
    prof_checks = []
    for t in TABLES:
        prof_checks += infer_spec(train_data[t], target_name=t).checks
    profiled = Spec(name="S3", checks=prof_checks)
    combined = Spec(name="S5", checks=list(mapping_spec.checks) + prof_checks)
    suites = {
        "S1 structural": structural,
        "S2 constraints": constraints,
        "S3 profiled": profiled,
        "S4 mapping": mapping_spec,
        "S5 mapping+profiled": combined,
    }
    for k, s in suites.items():
        s.name = k
    return suites, mappings


def e1_defects(suites, n, seeds, tmp):
    rows = []
    for seed in seeds:
        clean = run_pipeline(n, seed, tmp)
        live_by_suite = {
            name: {r.check.id for r in validate(spec, clean).results if r.passed} for name, spec in suites.items()
        }
        for d in T.DEFECTS:
            bad = run_pipeline(n, seed, tmp, {d})
            data = {**clean, **{t: bad[t] for t in TABLES}}
            for name, spec in suites.items():
                live = live_by_suite[name]
                res = validate(spec, data)
                killers = [r.check.id for r in res.results if not r.passed and r.check.id in live]
                rows.append(
                    {
                        "seed": seed,
                        "defect": d,
                        "suite": name,
                        "detected": bool(killers),
                        "n_failing": len(killers),
                        "first_killers": killers[:3],
                    }
                )
    return pd.DataFrame(rows)


def e2_mutation(suites, n, seeds, rates, tmp):
    rows, survivors = [], defaultdict(lambda: defaultdict(int))
    for seed in seeds:
        clean = run_pipeline(n, 100 + seed, tmp)
        for rate in rates:
            for table in TABLES:
                for name, spec in suites.items():
                    rep = mutation_test(spec, clean, dataset=table, rate=rate, seed=seed)
                    for r in rep.results:
                        rows.append(
                            {
                                "seed": seed,
                                "rate": rate,
                                "table": table,
                                "suite": name,
                                "operator": r.operator,
                                "fault_class": r.fault_class,
                                "column": r.column,
                                "killed": r.killed,
                            }
                        )
                        if not r.killed and name == "S4 mapping" and rate == 0.05:
                            survivors[f"{table}.{r.column or '(rows)'}"][r.operator] += 1
    return pd.DataFrame(rows), {k: dict(v) for k, v in survivors.items()}


def e3_false_positives(suites, n, seeds, tmp):
    rows = []
    for seed in seeds:
        clean = run_pipeline(n, seed, tmp)
        for name, spec in suites.items():
            res = validate(spec, clean)
            failing = [r.check.id for r in res.results if not r.passed]
            rows.append(
                {
                    "seed": seed,
                    "suite": name,
                    "false_alarm": bool(failing),
                    "n_failing": len(failing),
                    "n_checks": len(res.results),
                    "failing": failing[:5],
                }
            )
    return pd.DataFrame(rows)


def e4_scalability(sizes, tmp, mapping_spec, repeats=3):
    rows = []
    for n in sizes:
        land = tmp / f"scale_{n}"
        generate_data.generate(n, 3, land)
        for rep in range(repeats):
            t0 = time.perf_counter()
            master, elig = T.read_sources(land)
            t1 = time.perf_counter()
            docs = T.transform(master, elig)
            t2 = time.perf_counter()
            tables = flatten_documents(docs, "members", key="memberId")
            t3 = time.perf_counter()
            res = validate(mapping_spec, {"member_master": master, "elig_segments": elig, **tables})
            t4 = time.perf_counter()
            assert res.passed
            rows.append(
                {
                    "members": n,
                    "segments": len(elig),
                    "documents": len(docs),
                    "repeat": rep,
                    "decode_s": t1 - t0,
                    "flatten_s": t3 - t2,
                    "validate_s": t4 - t3,
                    "checks": len(res.results),
                }
            )
    return pd.DataFrame(rows)


def summarise(e1, e2, e3, e4, suites, coverage, survivors, meta):
    s = {"meta": meta, "suites": {k: len(v.checks) for k, v in suites.items()}, "coverage": coverage.to_dict()}
    det = e1.groupby(["suite", "defect"])["detected"].mean().unstack(0)
    s["e1_defects_detected"] = {k: int((det[k] == 1.0).sum()) for k in det.columns}
    s["e1_defects_partially_detected"] = {k: int(((det[k] > 0) & (det[k] < 1)).sum()) for k in det.columns}
    s["e1_detection_rate"] = {k: round(float(det[k].mean()), 4) for k in det.columns}
    s["e1_per_defect"] = {d: {k: float(det.loc[d, k]) for k in det.columns} for d in det.index}
    ms = e2.groupby(["suite", "rate", "seed"])["killed"].mean().groupby(["suite", "rate"]).agg(["mean", "std"])
    s["e2_mutation_score"] = {
        f"{suite}@{rate}": {"mean": round(float(r["mean"]), 4), "sd": round(float(r["std"]), 4)}
        for (suite, rate), r in ms.iterrows()
    }
    s["e2_mutants_per_run"] = int(e2.groupby(["seed", "rate", "suite"]).size().iloc[0])
    by_class = e2[e2.rate == 0.05].groupby(["fault_class", "suite"])["killed"].mean().unstack(1)
    s["e2_by_fault_class_rate_0.05"] = {
        c: {k: round(float(v), 4) for k, v in row.items()} for c, row in by_class.iterrows()
    }
    by_table = e2[e2.rate == 0.05].groupby(["table", "suite"])["killed"].mean().unstack(1)
    s["e2_by_table_rate_0.05"] = {c: {k: round(float(v), 4) for k, v in row.items()} for c, row in by_table.iterrows()}
    s["e2_S4_survivors_rate_0.05"] = survivors
    fp = e3.groupby("suite").agg(
        runs=("false_alarm", "size"), false_alarm_runs=("false_alarm", "sum"), mean_failing=("n_failing", "mean")
    )
    s["e3_false_positives"] = {
        k: {
            "runs": int(r.runs),
            "false_alarm_runs": int(r.false_alarm_runs),
            "mean_failing_checks": round(float(r.mean_failing), 3),
        }
        for k, r in fp.iterrows()
    }
    s["e3_profiled_failing_examples"] = sorted({c for f in e3[e3.suite == "S3 profiled"]["failing"] for c in f})[:15]
    sc = e4.groupby(["members", "segments", "checks"])[["decode_s", "flatten_s", "validate_s"]].median().reset_index()
    s["e4_scalability_median_s"] = sc.round(3).to_dict("records")
    return s


def figures(e2, e4, out: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    order = ["S1 structural", "S2 constraints", "S3 profiled", "S4 mapping", "S5 mapping+profiled"]
    colors = ["#9aa5b1", "#7b8794", "#e3a008", "#2563eb", "#0f766e"]
    d = e2[e2.rate == 0.05].groupby(["fault_class", "suite"])["killed"].mean().unstack(1)[order]
    classes = list(d.index)
    x = np.arange(len(classes))
    w = 0.16
    fig, ax = plt.subplots(figsize=(7.0, 2.8), dpi=200)
    for i, (s, c) in enumerate(zip(order, colors)):
        ax.bar(x + (i - 2) * w, d[s].values * 100, w, label=s, color=c)
    ax.set_xticks(x, classes)
    ax.set_ylabel("Mutants detected (%)")
    ax.set_ylim(0, 105)
    ax.legend(ncol=5, fontsize=7, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    fig.tight_layout()
    fig.savefig(out / "fig_mutation_by_fault_class.png")
    plt.close(fig)

    sc = e4.groupby("members")[["decode_s", "flatten_s", "validate_s"]].median()
    fig, ax = plt.subplots(figsize=(3.4, 2.6), dpi=200)
    for col, lab, c in (
        ("decode_s", "COBOL decode", "#9aa5b1"),
        ("flatten_s", "document flatten", "#e3a008"),
        ("validate_s", "validation (all checks)", "#2563eb"),
    ):
        ax.plot(sc.index, sc[col], marker="o", label=lab, color=c, linewidth=1.5, markersize=3)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Member records")
    ax.set_ylabel("Seconds (median)")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(out / "fig_scalability.png")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    a = p.parse_args()
    n = 500 if a.quick else 2000
    defect_seeds = [101, 102] if a.quick else [101, 102, 103, 104, 105]
    mut_seeds = [1, 2] if a.quick else [1, 2, 3]
    fp_seeds = list(range(201, 206 if a.quick else 221))
    sizes = [1000, 5000] if a.quick else [1000, 5000, 20000, 50000, 100000]
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "raw"
    tmp.mkdir(exist_ok=True)

    t0 = time.time()
    train = run_pipeline(n, 7, tmp)
    suites, mappings = build_suites(train)
    coverage = mapping_coverage(mappings, train)
    print({k: len(v.checks) for k, v in suites.items()}, flush=True)
    e1 = e1_defects(suites, n, defect_seeds, tmp)
    print("E1 done", flush=True)
    e2, survivors = e2_mutation(suites, n, mut_seeds, [0.01, 0.05], tmp)
    print("E2 done", flush=True)
    e3 = e3_false_positives(suites, n, fp_seeds, tmp)
    print("E3 done", flush=True)
    e4 = e4_scalability(sizes, tmp, suites["S4 mapping"], repeats=1 if a.quick else 3)
    print("E4 done", flush=True)
    meta = {
        "mapverity": mapverity.__version__,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "machine": platform.machine(),
        "processor_count": __import__("os").cpu_count(),
        "members_per_run": n,
        "defect_seeds": defect_seeds,
        "mutation_seeds": mut_seeds,
        "fp_seeds": fp_seeds,
        "training_seed": 7,
        "wall_clock_s": round(time.time() - t0, 1),
    }
    summary = summarise(e1, e2, e3, e4, suites, coverage, survivors, meta)
    for name, df in (("e1_defects", e1), ("e2_mutation", e2), ("e3_false_positives", e3), ("e4_scalability", e4)):
        df.to_csv(OUT / f"{name}.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    figures(e2, e4, OUT)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("e1_per_defect", "e2_S4_survivors_rate_0.05")},
            indent=1,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
