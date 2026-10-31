import json

import pandas as pd
import pytest

from mapverity import CheckSpec, GatePolicy, Spec, evaluate_gates, validate
from mapverity.reporting.allure import write_allure_results
from mapverity.reporting.html import render_html


def _result(fail=False, warn_fail=False):
    df = pd.DataFrame({"a": [1, 2, None if fail else 3], "b": ["x", "y", "z"]})
    checks = [
        CheckSpec("not_null", dataset="t", column="a"),
        CheckSpec(
            "allowed_values",
            dataset="t",
            column="b",
            severity="warn",
            params={"values": ["x", "y"] if warn_fail else ["x", "y", "z"]},
        ),
    ]
    return validate(Spec(checks=checks), {"t": df})


def test_integrity_failure_blocks_regardless_of_everything_else():
    d = evaluate_gates(GatePolicy(), result=_result(fail=True), risk={"t.a": 0.01})
    assert d.outcome == "BLOCK" and d.exit_code == 1


def test_clean_run_passes_and_warnings_warn():
    assert evaluate_gates(GatePolicy(), result=_result()).outcome == "PASS"
    d = evaluate_gates(GatePolicy(), result=_result(warn_fail=True))
    assert d.outcome == "WARN" and d.exit_code == 0
    d = evaluate_gates(GatePolicy(max_warn_failures=0), result=_result(warn_fail=True))
    assert d.outcome == "BLOCK"


def test_risk_is_advisory_unless_policy_opts_in():
    risk = {"members.gender": 0.95}
    assert evaluate_gates(GatePolicy(), result=_result(), risk=risk).outcome == "WARN"
    assert evaluate_gates(GatePolicy(risk_blocks=True), result=_result(), risk=risk).outcome == "BLOCK"


def test_unrun_checks_are_incomplete_never_pass():
    d = evaluate_gates(GatePolicy(), result=_result(), not_run=["x"])
    assert d.outcome == "INCOMPLETE" and d.exit_code == 1


def test_thresholds_and_missing_evidence():
    p = GatePolicy(min_value_coverage=0.9, min_mutation_score=0.8, mutation_action="WARN")
    assert (
        evaluate_gates(p, result=_result(), coverage={"value_coverage": 0.95}, mutation={"score": 0.85}).outcome
        == "PASS"
    )
    assert (
        evaluate_gates(p, result=_result(), coverage={"value_coverage": 0.5}, mutation={"score": 0.85}).outcome
        == "BLOCK"
    )
    assert (
        evaluate_gates(p, result=_result(), coverage={"value_coverage": 0.95}, mutation={"score": 0.1}).outcome
        == "WARN"
    )
    assert evaluate_gates(p, result=_result()).outcome == "INCOMPLETE"


def test_policy_file_rejects_unknown_keys(tmp_path):
    f = tmp_path / "g.yaml"
    f.write_text("gates:\n  min_value_coverage: 0.9\n  risk_blcoks: true\n")
    with pytest.raises(ValueError, match="risk_blcoks"):
        GatePolicy.load(f)


def test_html_report_escapes_and_contains_sections():
    r = _result(fail=True)
    r.results[0].outcome.message = "<script>alert(1)</script>"
    html = render_html(
        r,
        decision=evaluate_gates(GatePolicy(), result=r).to_dict(),
        coverage={"value_coverage": 0.9, "fields_in_mapping": 3, "value_validated": 3},
        mutation={
            "score": 0.5,
            "by_fault_class": {"accuracy": {"mutants": 2, "killed": 1, "score": 0.5}},
            "survivors": [{"operator": "case_change", "column": "b"}],
        },
        plan=[{"check": "x", "priority": 1.0, "contributions": {"risk": 0.4}}],
    )
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    for section in ("Quality gate", "Failures", "Mapping coverage", "Mutation testing", "Execution order"):
        assert section in html


def test_allure_results(tmp_path):
    n = write_allure_results(_result(fail=True), tmp_path)
    files = list(tmp_path.glob("*-result.json"))
    assert n == len(files) == 2
    statuses = sorted(json.loads(f.read_text())["status"] for f in files)
    assert statuses == ["failed", "passed"]


def test_every_registered_check_is_documented():
    from pathlib import Path

    from mapverity.validation.checks import REGISTRY

    doc = (Path(__file__).resolve().parents[1] / "docs" / "checks.md").read_text(encoding="utf-8")
    missing = [name for name in REGISTRY if f"## `{name}`" not in doc]
    assert not missing, f"run `python docs/gen_checks.py`; undocumented checks: {missing}"
