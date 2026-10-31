"""Explainable defect-risk model.

A gradient-boosted (XGBoost when installed, scikit-learn otherwise), random-forest or logistic
model over static code metrics, with log1p feature scaling and optional SMOTE oversampling applied
inside each training fold only (never to evaluation data). Requires `pip install 'mapverity[ai]'`.

    model = DefectRiskModel(estimator="xgboost", smote=True).fit(X_train, y_train)
    risk = model.predict_proba(X_new)                 # probability a unit is defect-prone
    contrib = model.explain(X_new)                    # per-unit, per-feature SHAP contributions

Risk scores are advisory inputs to prioritization and quality gates; they never override a
deterministic check failure (see core/gates.py).
"""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import numpy as np
import pandas as pd

ESTIMATORS = ("xgboost", "gbm", "random_forest", "logistic")


def _require_sklearn():
    try:
        import sklearn  # noqa: F401
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError("the defect-risk model needs scikit-learn: pip install 'mapverity[ai]'") from exc


def _make_estimator(kind: str, seed: int, pos_weight: float):
    _require_sklearn()
    if kind == "xgboost":
        try:
            from xgboost import XGBClassifier
        except ImportError:
            kind = "gbm"
        else:
            return XGBClassifier(
                n_estimators=200,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.9,
                colsample_bytree=0.8,
                min_child_weight=2,
                reg_lambda=1.0,
                scale_pos_weight=pos_weight,
                eval_metric="logloss",
                random_state=seed,
                n_jobs=1,
                tree_method="hist",
            )
    if kind == "gbm":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_depth=3,
            learning_rate=0.05,
            max_iter=200,
            class_weight="balanced" if pos_weight > 1 else None,
            random_state=seed,
        )
    if kind == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample", random_state=seed, n_jobs=1
        )
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    raise ValueError(f"unknown estimator '{kind}' (choose from {ESTIMATORS})")


@dataclass
class DefectRiskModel:
    estimator: str = "xgboost"
    smote: bool = False
    seed: int = 0
    features: List[str] = field(default_factory=list)
    _model: Any = None
    _scaler: Any = None
    _trained_on: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ fitting
    def _prep(self, X: pd.DataFrame, fit: bool = False) -> np.ndarray:
        arr = np.log1p(np.clip(X[self.features].to_numpy(dtype=float), 0, None))
        if self.estimator == "logistic":
            from sklearn.preprocessing import StandardScaler

            if fit:
                self._scaler = StandardScaler().fit(arr)
            arr = self._scaler.transform(arr)
        return arr

    def fit(self, X: pd.DataFrame, y: Iterable[int], sources: Optional[List[str]] = None) -> "DefectRiskModel":
        yy = np.asarray(list(y), dtype=int)
        if not self.features:
            self.features = list(X.columns)
        Xp = self._prep(X, fit=True)
        if self.smote and yy.sum() >= 2:
            try:
                from imblearn.over_sampling import SMOTE
            except ImportError as exc:  # pragma: no cover - optional extra
                raise ImportError("smote=True needs imbalanced-learn: pip install 'mapverity[ai]'") from exc
            positives = int(yy.sum())
            k = min(5, positives - 1)
            Xp, yy = SMOTE(k_neighbors=max(k, 1), random_state=self.seed).fit_resample(Xp, yy)
            pos_weight = 1.0
        else:
            positives = int(yy.sum())
            pos_weight = float(len(yy) - positives) / max(positives, 1)
        self._model = _make_estimator(self.estimator, self.seed, pos_weight)
        self._model.fit(Xp, yy)
        self._trained_on = {
            "rows": int(len(yy)),
            "positives": int(yy.sum()),
            "sources": sources or [],
            "estimator": type(self._model).__name__,
            "smote": self.smote,
            "features": self.features,
        }
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("model is not fitted")
        return self._model.predict_proba(self._prep(X))[:, 1]

    # ------------------------------------------------------------------ explanation
    def explain(self, X: pd.DataFrame, method: str = "auto") -> pd.DataFrame:
        """Per-row, per-feature contributions to the prediction (log-odds for trees/logistic).

        method: shap | linear | permutation | auto (SHAP when available)."""
        from .explain import contributions

        return contributions(self, X, method)

    # ------------------------------------------------------------------ persistence
    def save(self, path: Union[str, Path]) -> str:
        """Pickle the model and write `<path>.json` with its metadata and SHA-256.
        Only load model files you created or trust: unpickling runs code."""
        path = Path(path)
        blob = pickle.dumps(self)
        path.write_bytes(blob)
        digest = hashlib.sha256(blob).hexdigest()
        meta = {**self._trained_on, "sha256": digest}
        path.with_suffix(path.suffix + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return digest

    @classmethod
    def load(cls, path: Union[str, Path], expected_sha256: Optional[str] = None) -> "DefectRiskModel":
        path = Path(path)
        blob = path.read_bytes()
        meta_path = path.with_suffix(path.suffix + ".json")
        digest = hashlib.sha256(blob).hexdigest()
        expected = expected_sha256 or (json.loads(meta_path.read_text())["sha256"] if meta_path.exists() else None)
        if expected is None or digest != expected:
            raise ValueError("model checksum missing or mismatched; refusing to unpickle an unverified file")
        obj = pickle.loads(blob)  # noqa: S301 - integrity-checked above
        if not isinstance(obj, cls):
            raise TypeError("file does not contain a DefectRiskModel")
        return obj


# ---------------------------------------------------------------------- evaluation helpers


def classification_metrics(y_true: Iterable[int], prob: Iterable[float], threshold: float = 0.5) -> Dict[str, float]:
    from sklearn.metrics import (
        average_precision_score,
        f1_score,
        matthews_corrcoef,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y = np.asarray(list(y_true), dtype=int)
    p = np.asarray(list(prob), dtype=float)
    pred = (p >= threshold).astype(int)
    out = {"auc": float("nan"), "pr_auc": float("nan")}
    if 0 < y.sum() < len(y):
        out["auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
    out.update(
        {
            "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)),
            "f1": float(f1_score(y, pred, zero_division=0)),
            "mcc": float(matthews_corrcoef(y, pred)) if len(set(pred)) > 1 or len(set(y)) > 1 else 0.0,
            "positive_rate": float(y.mean()),
        }
    )
    return out


def cross_validate(
    df: pd.DataFrame,
    features: List[str],
    label: str = "Defective",
    estimator: str = "xgboost",
    smote: bool = False,
    folds: int = 5,
    repeats: int = 5,
    seed: int = 0,
) -> pd.DataFrame:
    """Repeated stratified k-fold; SMOTE (if on) is fitted inside each training fold only."""
    from sklearn.model_selection import RepeatedStratifiedKFold

    X, y = df[features], df[label].to_numpy()
    positives = int(y.sum())
    n_splits = min(folds, max(2, positives), max(2, len(y) - positives))
    rskf = RepeatedStratifiedKFold(n_splits=n_splits, n_repeats=repeats, random_state=seed)
    rows = []
    for i, (tr, te) in enumerate(rskf.split(X, y)):
        m = DefectRiskModel(estimator=estimator, smote=smote, seed=seed + i, features=list(features))
        m.fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[te])
        rows.append({"fold": i, **classification_metrics(y[te], p)})
    return pd.DataFrame(rows)
