"""Explanations for the defect-risk model, and how stable they are.

* `contributions`  per-row feature contributions: SHAP (TreeExplainer / LinearExplainer) when the
                   `shap` package is installed, exact linear contributions for logistic models,
                   permutation importance otherwise.
* `lime_explain`   a LIME local explanation for one unit (optional dependency `lime`).
* `explanation_stability`  agreement of global feature rankings across resamples
                   (mean pairwise Kendall tau and top-k Jaccard): an explanation that changes with
                   every retrain is not one a reviewer should act on.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Dict, List, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover
    from .predictor import DefectRiskModel


def contributions(model: "DefectRiskModel", X: pd.DataFrame, method: str = "auto") -> pd.DataFrame:
    Xp = model._prep(X)
    inner = model._model
    if method in ("auto", "shap"):
        try:
            import shap
        except ImportError:
            if method == "shap":
                raise
        else:
            if model.estimator == "logistic":
                explainer = shap.LinearExplainer(inner, np.zeros((1, Xp.shape[1])))
            else:
                explainer = shap.TreeExplainer(inner)
            vals = explainer.shap_values(Xp)
            if isinstance(vals, list):  # older shap: one array per class
                vals = vals[1]
            vals = np.asarray(vals)
            if vals.ndim == 3:
                vals = vals[:, :, 1]
            return pd.DataFrame(vals, columns=model.features, index=X.index)
    if model.estimator == "logistic" and method in ("auto", "linear"):
        return pd.DataFrame(Xp * inner.coef_[0], columns=model.features, index=X.index)
    # permutation fallback: global importance broadcast to rows (no local attribution available)
    from sklearn.inspection import permutation_importance

    y_hat = (model.predict_proba(X) >= 0.5).astype(int)
    if len(set(y_hat)) < 2:
        imp = np.zeros(len(model.features))
    else:
        imp = permutation_importance(inner, Xp, y_hat, n_repeats=5, random_state=0).importances_mean
    return pd.DataFrame(np.tile(imp, (len(X), 1)), columns=model.features, index=X.index)


def global_importance(contrib: pd.DataFrame) -> pd.Series:
    """Mean |contribution| per feature, descending."""
    return contrib.abs().mean().sort_values(ascending=False)


def top_reasons(contrib: pd.DataFrame, k: int = 3) -> List[List[str]]:
    """Plain-language top-k drivers per row, e.g. ['CYCLOMATIC_COMPLEXITY +0.41', ...]."""
    out = []
    for _, row in contrib.iterrows():
        top = row.reindex(row.abs().sort_values(ascending=False).index)[:k]
        out.append([f"{f} {v:+.2f}" for f, v in top.items()])
    return out


def explanation_stability(importances: Sequence[pd.Series], k: int = 5) -> Dict[str, float]:
    from scipy.stats import kendalltau

    taus, jacc = [], []
    for a, b in itertools.combinations(importances, 2):
        feats = list(a.index)
        tau = kendalltau(a.reindex(feats).to_numpy(), b.reindex(feats).to_numpy()).statistic
        taus.append(float(tau) if tau == tau else 0.0)
        ta, tb = set(a.sort_values(ascending=False).index[:k]), set(b.sort_values(ascending=False).index[:k])
        jacc.append(len(ta & tb) / len(ta | tb))
    return {
        "kendall_tau_mean": float(np.mean(taus)) if taus else float("nan"),
        f"top{k}_jaccard_mean": float(np.mean(jacc)) if jacc else float("nan"),
        "pairs": len(taus),
    }


def lime_explain(model: "DefectRiskModel", X_background: pd.DataFrame, row: pd.Series, num_features: int = 5):
    try:
        from lime.lime_tabular import LimeTabularExplainer
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError("LIME explanations need: pip install lime") from exc
    explainer = LimeTabularExplainer(
        X_background[model.features].to_numpy(dtype=float),
        feature_names=model.features,
        class_names=["clean", "defect-prone"],
        discretize_continuous=True,
        random_state=0,
    )
    exp = explainer.explain_instance(
        row[model.features].to_numpy(dtype=float),
        lambda a: np.column_stack(
            [
                1 - model.predict_proba(pd.DataFrame(a, columns=model.features)),
                model.predict_proba(pd.DataFrame(a, columns=model.features)),
            ]
        ),
        num_features=num_features,
    )
    return exp.as_list()
