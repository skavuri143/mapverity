import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mapverity import Spec, infer_spec, mutation_test, spec_from_mapping, validate
from mapverity.validation.mapping import FieldMapping, load_mapping, mapping_coverage, rule_to_expression

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "member_eligibility"


def fm(**kw):
    base = dict(target_table="t", target_field="f", source_dataset="s", source_field="c")
    base.update(kw)
    return FieldMapping(**base)


@pytest.mark.parametrize(
    "rule,expected",
    [
        ("", 'col("c")'),
        ("trim", 'strip(col("c"))'),
        ("upper_trim", 'upper(strip(col("c")))'),
        ("date: %Y%m%d", 'to_date(col("c"), "%Y%m%d")'),
        ("lookup: M=MALE, *=UNKNOWN", 'lookup(col("c"), {"M": "MALE"}, \'UNKNOWN\')'),
        ("lookup: M=MALE", 'lookup(col("c"), {"M": "MALE"}, None)'),
        ("concat: a, b", 'concat(col("a"), col("b"))'),
        ("constant: X", '"X"'),
        ("none", None),
    ],
)
def test_rule_translation(rule, expected):
    assert rule_to_expression(fm(rule=rule)) == expected


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        rule_to_expression(fm(rule="magic: x"))


def _people(n=200, seed=0):
    rng = np.random.default_rng(seed)
    src = pd.DataFrame(
        {
            "person_id": [f"P{i:05d}" for i in range(n)],
            "name": rng.choice(["ann", "bob", "cy"], n),
            "status": rng.choice(["A", "A", "I"], n),
            "start": pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
            "amount": rng.normal(100, 15, n).round(2),
        }
    )
    src["end"] = (pd.to_datetime(src["start"]) + pd.to_timedelta(rng.integers(1, 300, n), unit="D")).dt.strftime(
        "%Y-%m-%d"
    )
    tgt = src[src["status"] == "A"].copy()
    tgt["name"] = tgt["name"].str.upper()
    return src, tgt.drop(columns=["status"]).reset_index(drop=True)


def test_infer_mines_key_filter_mapping_and_ordering():
    src, tgt = _people()
    spec = infer_spec(tgt, src)
    types = {(c.type, c.column) for c in spec.checks}
    assert ("unique", "person_id") in types
    assert ("mapping", "name") in types
    kinds = {c.type for c in spec.checks}
    assert {"key_match", "row_count_match", "row_rule"} <= kinds
    rc = next(c for c in spec.checks if c.type == "row_count_match")
    assert "status" in rc.params["source_filter"]
    assert validate(spec, {"source": src, "target": tgt}).passed


def test_mutation_score_rewards_stronger_spec():
    src, tgt = _people()
    data = {"source": src, "target": tgt}
    weak = Spec(checks=[c for c in infer_spec(tgt).checks if c.type == "schema"])
    strong = infer_spec(tgt, src)
    w = mutation_test(weak, data, rate=0.05, seed=1)
    s = mutation_test(strong, data, rate=0.05, seed=1)
    assert s.total == w.total and s.total > 10
    assert s.score > w.score
    assert all(r.killed_by == [] for r in w.results if not r.killed)


def test_example_pipeline_end_to_end(tmp_path):
    sys.path.insert(0, str(EXAMPLE))
    import generate_data
    import transform as T

    from mapverity.validation.documents import flatten_documents

    generate_data.generate(300, 11, tmp_path)
    master, elig = T.read_sources(tmp_path)
    mappings = load_mapping(EXAMPLE / "mapping" / "member_data_model_mapping.csv")
    spec = spec_from_mapping(mappings)
    good = flatten_documents(T.transform(master, elig), "members", key="memberId")
    data = {"member_master": master, "elig_segments": elig, **good}
    res = validate(spec, data)
    assert res.passed, [r.to_dict() for r in res.failures]
    cov = mapping_coverage(mappings, good)
    assert cov.constraint_only == ["members.source.loadTimestamp"]
    for defect in ("D07_comp3_scale_lost", "D13_segments_inner_join_drops_last", "D03_name_not_trimmed"):
        bad = flatten_documents(T.transform(master, elig, {defect}), "members", key="memberId")
        assert not validate(spec, {**data, **bad}).passed, defect


def test_cli_and_pytest_plugin(tmp_path):
    src, tgt = _people(80)
    src.to_csv(tmp_path / "src.csv", index=False)
    tgt.to_csv(tmp_path / "tgt.csv", index=False)

    def run(*a):
        return subprocess.run([sys.executable, "-m", "mapverity.cli", *a], cwd=tmp_path, capture_output=True, text=True)

    r = run("infer", "--target", "tgt.csv", "--source", "src.csv", "-o", "people.mapverity.yaml")
    assert r.returncode == 0, r.stderr
    r = run("validate", "people.mapverity.yaml", "--junit", "out.xml", "--json", "out.json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((tmp_path / "out.json").read_text())["passed"] is True
    assert "<testsuites" in (tmp_path / "out.xml").read_text()
    r = run("mutate", "people.mapverity.yaml", "--rate", "0.05", "--json", "m.json")
    assert r.returncode == 0 and json.loads((tmp_path / "m.json").read_text())["mutants"] > 0
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)], capture_output=True, text=True, cwd=tmp_path
    )
    assert r.returncode == 0, r.stdout[-2000:]
    assert "passed" in r.stdout
