"""E5: explainable defect-risk prediction on the 12 cleaned NASA MDP projects.

    python experiments/erad_qa/e5_defect_prediction.py [--quick]

* within-project: repeated stratified 5-fold CV (3 repeats) per project
* cross-project: leave-one-project-out (train on 11, test on 1), the setting in which a model
  trained on public data is applied to a new codebase
* configurations: logistic regression, random forest, XGBoost, XGBoost + SMOTE (SMOTE fitted
  inside training folds only); features = the 18 metrics MapVerity can also compute from Python
* explanation stability: SHAP global importance per fold, mean pairwise Kendall tau and top-5
  Jaccard across folds (XGBoost)

Reports means with 95% confidence intervals (t-interval over folds within a project; over
projects for summaries). Writes experiments/erad_qa/results/e5_*.csv and e5_summary.json.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import mapverity
from mapverity.ai.datasets import LABEL, PORTABLE_FEATURES, load_nasa_mdp
from mapverity.ai.explain import explanation_stability, global_importance
from mapverity.ai.predictor import DefectRiskModel, classification_metrics

OUT = Path(__file__).resolve().parent / "results"
CONFIGS = [("logistic", False), ("random_forest", False), ("xgboost", False), ("xgboost", True)]
METRICS = ["auc", "pr_auc", "f1", "mcc", "recall", "precision"]


def ci95(x) -> tuple:
    x = np.asarray([v for v in x if v == v], dtype=float)
    if len(x) < 2:
        return (float(x.mean()) if len(x) else float("nan"), float("nan"))
    half = stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return float(x.mean()), float(half)


def within_project(frames, repeats, seed=0):
    from sklearn.model_selection import RepeatedStratifiedKFold

    rows, stab = [], {}
    for name, df in frames.items():
        X, y = df[PORTABLE_FEATURES], df[LABEL].to_numpy()
        k = int(min(5, y.sum(), len(y) - y.sum()))
        splits = list(RepeatedStratifiedKFold(n_splits=k, n_repeats=repeats, random_state=seed).split(X, y))
        importances = []
        for est, smote in CONFIGS:
            for i, (tr, te) in enumerate(splits):
                m = DefectRiskModel(estimator=est, smote=smote, seed=seed + i, features=PORTABLE_FEATURES)
                m.fit(X.iloc[tr], y[tr])
                p = m.predict_proba(X.iloc[te])
                rows.append(
                    {"project": name, "estimator": est, "smote": smote, "fold": i, **classification_metrics(y[te], p)}
                )
                if est == "xgboost" and not smote:
                    importances.append(global_importance(m.explain(X.iloc[te], method="shap")))
        stab[name] = explanation_stability(importances, k=5)
        print(f"  within {name}: done", flush=True)
    return pd.DataFrame(rows), stab


def cross_project(frames, seed=0):
    rows = []
    for name in frames:
        train = pd.concat([df for n, df in frames.items() if n != name], ignore_index=True)
        test = frames[name]
        for est, smote in CONFIGS:
            m = DefectRiskModel(estimator=est, smote=smote, seed=seed, features=PORTABLE_FEATURES)
            m.fit(train[PORTABLE_FEATURES], train[LABEL])
            p = m.predict_proba(test[PORTABLE_FEATURES])
            rows.append({"project": name, "estimator": est, "smote": smote, **classification_metrics(test[LABEL], p)})
        print(f"  cross {name}: done", flush=True)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    frames = load_nasa_mdp(["PC4", "KC1", "CM1"] if a.quick else None, features=PORTABLE_FEATURES)
    within, stab = within_project(frames, repeats=1 if a.quick else 3)
    cross = cross_project(frames)
    within.to_csv(OUT / "e5_within_project_folds.csv", index=False)
    cross.to_csv(OUT / "e5_cross_project.csv", index=False)

    def label(e, s):
        return f"{e}{'+smote' if s else ''}"

    summary = {
        "meta": {
            "mapverity": mapverity.__version__,
            "python": platform.python_version(),
            "projects": {n: {"modules": int(len(df)), "defective": int(df[LABEL].sum())} for n, df in frames.items()},
            "features": PORTABLE_FEATURES,
            "repeats": 1 if a.quick else 3,
            "wall_clock_s": round(time.time() - t0, 1),
        }
    }
    per_project = within.groupby(["project", "estimator", "smote"])[METRICS].mean().reset_index()
    summary["within_project_mean_over_projects"] = {
        label(e, s): {m: dict(zip(("mean", "ci95"), ci95(g[m]))) for m in METRICS}
        for (e, s), g in per_project.groupby(["estimator", "smote"])
    }
    summary["within_project_per_project_auc"] = {
        p: {label(e, s): round(float(v), 4) for (e, s), v in g.set_index(["estimator", "smote"])["auc"].items()}
        for p, g in per_project.groupby("project")
    }
    summary["cross_project_mean_over_projects"] = {
        label(e, s): {m: dict(zip(("mean", "ci95"), ci95(g[m]))) for m in METRICS}
        for (e, s), g in cross.groupby(["estimator", "smote"])
    }
    summary["cross_project_auc"] = {
        p: {label(e, s): round(float(v), 4) for (e, s), v in g.set_index(["estimator", "smote"])["auc"].items()}
        for p, g in cross.groupby("project")
    }
    summary["explanation_stability_xgboost"] = stab
    tau = [v["kendall_tau_mean"] for v in stab.values()]
    jac = [v["top5_jaccard_mean"] for v in stab.values()]
    summary["explanation_stability_overall"] = {
        "kendall_tau": dict(zip(("mean", "ci95"), ci95(tau))),
        "top5_jaccard": dict(zip(("mean", "ci95"), ci95(jac))),
    }
    # Wilcoxon: XGBoost vs logistic, per project mean AUC (within-project)
    piv = per_project.pivot_table(index="project", columns=["estimator", "smote"], values="auc")
    w = stats.wilcoxon(piv[("xgboost", False)], piv[("logistic", False)])
    summary["wilcoxon_within_auc_xgboost_vs_logistic"] = {"statistic": float(w.statistic), "p": float(w.pvalue)}
    piv_s = per_project.pivot_table(index="project", columns=["estimator", "smote"], values="mcc")
    w2 = stats.wilcoxon(piv_s[("xgboost", True)], piv_s[("xgboost", False)])
    summary["wilcoxon_within_mcc_smote_vs_none"] = {"statistic": float(w2.statistic), "p": float(w2.pvalue)}
    (OUT / "e5_summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "within_project_mean_over_projects",
                    "cross_project_mean_over_projects",
                    "explanation_stability_overall",
                )
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
