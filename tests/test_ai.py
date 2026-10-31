import json
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from mapverity import CheckSpec, Spec  # noqa: E402
from mapverity.ai.codemetrics import function_metrics, metrics_frame  # noqa: E402
from mapverity.ai.datasets import NASA_MDP, PORTABLE_FEATURES, parse_arff  # noqa: E402
from mapverity.ai.predictor import DefectRiskModel, classification_metrics, cross_validate  # noqa: E402
from mapverity.ai.prioritizer import (  # noqa: E402
    ERADPrioritizer,
    apfd,
    apfdc,
    cost_to_first_detection,
    detected_within_budget,
    run_plan,
    units_of,
)
from mapverity.ai.signals import ProfileProbe, RunHistory, data_unit_risk, diff_mappings, load_ownership  # noqa: E402


def synthetic_modules(n=400, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({f: rng.gamma(2.0, 5.0, n) for f in PORTABLE_FEATURES})
    signal = np.log1p(df["CYCLOMATIC_COMPLEXITY"]) + np.log1p(df["LOC_TOTAL"]) * 0.5
    p = 1 / (1 + np.exp(-(signal - signal.mean()) * 3))
    df["Defective"] = (rng.random(n) < p).astype(int)
    return df


ARFF = """@relation T
@attribute LOC_TOTAL numeric
@attribute CYCLOMATIC_COMPLEXITY numeric
@attribute label {Y,N}
@data
10,2,N
120,15,Y
"""


def test_parse_arff_normalises_label():
    df = parse_arff(ARFF)
    assert df["Defective"].tolist() == [0, 1] and df["LOC_TOTAL"].tolist() == [10, 120]
    assert len(NASA_MDP) == 12 and all(len(v) == 64 for v in NASA_MDP.values())


@pytest.mark.parametrize("estimator,smote", [("xgboost", False), ("random_forest", True), ("logistic", False)])
def test_model_learns_signal_and_explains(estimator, smote, tmp_path):
    df = synthetic_modules()
    res = cross_validate(df, PORTABLE_FEATURES, estimator=estimator, smote=smote, folds=3, repeats=1)
    assert res["auc"].mean() > 0.7
    m = DefectRiskModel(estimator=estimator, smote=smote).fit(df[PORTABLE_FEATURES], df["Defective"])
    contrib = m.explain(df[PORTABLE_FEATURES].head(20))
    assert contrib.shape == (20, len(PORTABLE_FEATURES))
    imp = contrib.abs().mean().sort_values(ascending=False)
    assert {"CYCLOMATIC_COMPLEXITY", "LOC_TOTAL", "BRANCH_COUNT"} & set(imp.index[:6])
    path = tmp_path / "m.pkl"
    m.save(path)
    m2 = DefectRiskModel.load(path)
    assert np.allclose(m.predict_proba(df[PORTABLE_FEATURES].head()), m2.predict_proba(df[PORTABLE_FEATURES].head()))


def test_model_load_refuses_tampered_file(tmp_path):
    df = synthetic_modules(100)
    m = DefectRiskModel(estimator="logistic").fit(df[PORTABLE_FEATURES], df["Defective"])
    path = tmp_path / "m.pkl"
    m.save(path)
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum"):
        DefectRiskModel.load(path)


def test_classification_metrics_edge_cases():
    m = classification_metrics([0, 1, 1, 0], [0.1, 0.9, 0.6, 0.4])
    assert m["auc"] == 1.0 and m["precision"] == 1.0
    assert math.isnan(classification_metrics([0, 0], [0.2, 0.3])["auc"])


def test_codemetrics_for_python_functions(tmp_path):
    src = """
def simple(x):
    return x + 1

def branchy(x, y):
    # decide
    if x > 1 and y:
        for i in range(3):
            x += i
    elif y:
        x = 0
    return x
"""
    fms = {f.unit: f.metrics for f in function_metrics(src)}
    assert fms["simple"]["CYCLOMATIC_COMPLEXITY"] == 1 and fms["simple"]["BRANCH_COUNT"] == 1
    assert fms["branchy"]["CYCLOMATIC_COMPLEXITY"] == 5
    assert fms["branchy"]["LOC_COMMENTS"] == 1
    assert fms["branchy"]["HALSTEAD_VOLUME"] > fms["simple"]["HALSTEAD_VOLUME"] > 0
    (tmp_path / "t.py").write_text(src)
    df = metrics_frame(tmp_path)
    assert set(df["unit"]) == {"t:simple", "t:branchy"} and set(PORTABLE_FEATURES) <= set(df.columns)


def test_history_profile_drift_and_mapping_diff(tmp_path):
    h = RunHistory()
    for i in range(10):
        h.record("a", failed=i >= 7, seconds=0.5)
        h.record("b", failed=False, seconds=2.0)
    assert h.failure_rate("a") > h.failure_rate("b") and h.last_failed("a") and not h.last_failed("b")
    assert h.median_seconds("b") == 2.0
    h.save(tmp_path / "h.json")
    assert RunHistory.load(tmp_path / "h.json").runs == h.runs

    base = pd.DataFrame({"name": ["Ann", "Bob", "Cy"] * 50, "amt": np.linspace(10, 20, 150)})
    probe = ProfileProbe.from_data({"t": base})
    same = probe.drift({"t": base.copy()})
    assert max(same.values()) < 0.05
    drifted = probe.drift({"t": base.assign(name=base["name"].str.upper(), amt=base["amt"] * 100)})
    assert drifted["t.name"] > 0.5 and drifted["t.amt"] > 0.5

    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_text("target_table,target_field,source_field,rule\nm,a,x,trim\nm,b,y,upper\nm,c,z,trim\n")
    new.write_text("target_table,target_field,source_field,rule\nm,a,x,trim\nm,b,y,lower\nm,d,w,trim\n")
    assert diff_mappings(old, new) == {"added": ["m.d"], "removed": ["m.c"], "changed": ["m.b"]}


def test_ownership_propagates_risk(tmp_path):
    f = tmp_path / "own.yaml"
    f.write_text("t:map_a: [m.a, m.full]\nt:map_b: [m.b, m.full]\n")
    risk = data_unit_risk({"t:map_a": 0.2, "t:map_b": 0.7}, load_ownership(f))
    assert risk == {"m.a": 0.2, "m.full": 0.7, "m.b": 0.7}


def test_erad_prioritizer_orders_explains_and_runs_within_budget():
    df = pd.DataFrame({"a": [1, 2, None], "b": ["x", "y", "z"], "c": [1, 1, 1]})
    spec = Spec(
        checks=[
            CheckSpec("not_null", dataset="t", column="c"),
            CheckSpec("not_null", dataset="t", column="a"),
            CheckSpec("allowed_values", dataset="t", column="b", params={"values": ["x", "y", "z"]}),
        ]
    )
    assert units_of(spec.checks[1]) == ["t.a"]
    plan = ERADPrioritizer().plan(spec, changed={"t.a"}, risk={"t.a": 0.9, "t.b": 0.9})
    assert plan[0].check == "not_null:t:a"
    assert plan[0].contributions["change"] > 0 and plan[0].contributions["risk"] > plan[2].contributions["risk"]
    result, not_run = run_plan(spec, plan, {"t": df}, stop_on_failure=True)
    assert [r.check.id for r in result.results] == ["not_null:t:a"] and len(not_run) == 2
    result, not_run = run_plan(spec, plan, {"t": df}, budget_seconds=0.0)
    assert not result.results and len(not_run) == 3


def test_fit_weights_learns_informative_signal():
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(300):
        f = {
            "hist": rng.random() * 0.2,
            "risk": rng.random(),
            "change": float(rng.random() < 0.3),
            "drift": rng.random() * 0.1,
            "last": 0.0,
        }
        rows.append((f, bool(f["change"] and rng.random() < 0.8)))
    w = ERADPrioritizer().fit_weights(rows)
    assert w.change == max(w.as_dict().values())


def test_apfd_family():
    detects = {"f1": ["t3"], "f2": ["t1", "t3"]}
    cost = {"t1": 1.0, "t2": 1.0, "t3": 1.0}
    best, worst = ["t1", "t3", "t2"], ["t2", "t1", "t3"]
    assert apfd(best, detects) > apfd(worst, detects)
    assert apfd(best, detects) == pytest.approx(1 - (1 + 2) / (3 * 2) + 1 / 6)
    assert apfdc(best, detects, cost) == pytest.approx(apfd(best, detects))  # equal costs, equal severities
    assert cost_to_first_detection(best, detects, cost) == 1.0
    assert detected_within_budget(best, detects, cost, 0.34) == 0.5
    assert math.isnan(apfd(best, {}))


def test_rule_suggestions_are_verified_before_acceptance():
    from mapverity.ai.llm import suggest_checks

    df = pd.DataFrame({"start": ["2024-01-01", "2024-02-01"], "end": ["2024-03-01", "2024-02-15"], "x": [1, 2]})
    canned = json.dumps(
        [
            {"type": "row_rule", "dataset": "t", "expression": 'date(col("start")) <= date(col("end"))'},
            {"type": "in_range", "dataset": "t", "column": "x", "min": 5},
            {"type": "nonsense", "dataset": "t"},
        ]
    )
    out = suggest_checks(Spec(), {"t": df}, complete=lambda prompt: canned)
    assert [s.accepted for s in out] == [True, False, False]
