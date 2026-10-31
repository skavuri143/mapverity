import pandas as pd
import pytest

from mapverity import CheckSpec, Spec, validate
from mapverity.validation.documents import flatten_documents


@pytest.fixture
def data():
    src = pd.DataFrame(
        {
            "id": ["1", "2", "3", "4"],
            "first": [" ann", "BOB", "cy", "dee"],
            "amt": [10.0, 20.5, 30.0, 5.0],
            "st": ["A", "A", "T", "A"],
            "dob": ["19900131", "19851201", "20000101", "19770707"],
        }
    )
    tgt = pd.DataFrame(
        {
            "id": [1, 2, 4],
            "first": ["Ann", "Bob", "Dee"],
            "amt": [10.0, 20.5, 5.0],
            "dob": ["1990-01-31", "1985-12-01", "1977-07-07"],
            "start": ["2024-01-01", "2024-02-01", "2024-03-01"],
            "end": ["2024-06-01", None, "2024-02-15"],
        }
    )
    return {"source": src, "target": tgt}


def run(data, **kw):
    check = CheckSpec.from_dict(kw)
    return validate(Spec(checks=[check]), data).results[0]


def test_not_null_and_unique(data):
    assert run(data, type="not_null", dataset="target", column="first").passed
    r = run(data, type="not_null", dataset="target", column="end")
    assert not r.passed and r.outcome.failed == 1
    assert run(data, type="unique", dataset="target", column="id").passed


def test_row_rule_nulls_pass_and_violations_fail(data):
    r = run(data, type="row_rule", dataset="target", expression='date(col("start")) <= date(col("end"))')
    assert r.outcome.failed == 1  # 2024-03-01 > 2024-02-15


def test_mapping_with_filter_and_rules(data):
    ok = run(
        data,
        type="mapping",
        dataset="target",
        column="first",
        source="source",
        key="id",
        expression='title(strip(col("first")))',
    )
    assert ok.passed, ok.outcome.message
    ok = run(
        data,
        type="mapping",
        dataset="target",
        column="dob",
        source="source",
        key="id",
        expression='to_date(col("dob"), "%Y%m%d")',
    )
    assert ok.passed, ok.outcome.message


def test_mapping_is_exact_about_whitespace_and_case(data):
    data["target"].loc[0, "first"] = "Ann "
    r = run(
        data,
        type="mapping",
        dataset="target",
        column="first",
        source="source",
        key="id",
        expression='title(strip(col("first")))',
    )
    assert r.outcome.failed == 1
    assert r.outcome.sample[0] == {"key": "1", "actual": "Ann ", "expected": "Ann"}


def test_numeric_strings_are_not_compared_numerically():
    src = pd.DataFrame({"k": ["1"], "c": ["07"]})
    tgt = pd.DataFrame({"k": ["1"], "c": ["7"]})
    r = run(
        {"source": src, "target": tgt},
        type="mapping",
        dataset="target",
        column="c",
        source="source",
        key="k",
        expression='col("c")',
    )
    assert not r.passed


def test_key_match_with_filter(data):
    r = run(
        data,
        type="key_match",
        dataset="target",
        source="source",
        target="target",
        key="id",
        source_filter='col("st") == "A"',
    )
    assert r.passed, r.outcome.message
    r = run(data, type="key_match", dataset="target", source="source", target="target", key="id")
    assert r.outcome.failed == 1


def test_row_count_and_aggregate(data):
    assert run(data, type="row_count_match", dataset="target", source="source", source_filter='col("st") == "A"').passed
    assert run(
        data, type="aggregate_match", dataset="target", source="source", column="amt", source_filter='col("st") == "A"'
    ).passed


def test_composite_key_with_expression():
    src = pd.DataFrame({"m": ["A", "A"], "s": ["20240101", "20240201"], "v": ["x", "y"]})
    tgt = pd.DataFrame({"m": ["A", "A"], "d": ["2024-01-01", "2024-02-01"], "v": ["x", "y"]})
    r = run(
        {"source": src, "target": tgt},
        type="mapping",
        dataset="target",
        column="v",
        source="source",
        source_key=["m", 'to_date(col("s"), "%Y%m%d")'],
        target_key=["m", "d"],
        expression='col("v")',
    )
    assert r.passed and r.outcome.total == 2


def test_group_aggregate_counts_array_elements():
    docs = [{"id": "1", "segs": [{"a": 1}, {"a": 2}]}, {"id": "2", "segs": []}]
    t = flatten_documents(docs, "m", key="id")
    src = pd.DataFrame({"id": ["1", "1"]})
    r = run(
        {"m": t["m"], "src": src},
        type="group_aggregate",
        dataset="m",
        column="segs._count",
        source="src",
        key="id",
        expression="1",
        agg="count",
    )
    assert r.passed


def test_schema_and_regex_and_range(data):
    assert run(data, type="schema", dataset="target", columns={"id": "integer", "dob": "date", "amt": "number"}).passed
    r = run(data, type="schema", dataset="target", columns={"first": "integer"})
    assert not r.passed
    assert run(data, type="regex", dataset="target", column="dob", pattern=r"\d{4}-\d{2}-\d{2}").passed
    assert not run(data, type="in_range", dataset="target", column="amt", min=6).passed


def test_unknown_check_is_error_not_crash(data):
    r = run(data, type="nope", dataset="target")
    assert r.status == "error"


def test_flatten_nested_arrays_and_extended_json():
    docs = [
        {
            "_id": {"$oid": "abc"},
            "n": {"f": "A"},
            "d": {"$date": "2024-01-01T00:00:00Z"},
            "arr": [{"x": 1, "sub": [{"y": 2}]}],
            "tags": ["a", "b"],
        }
    ]
    t = flatten_documents(docs, "c")
    assert t["c"].loc[0, "_id"] == "abc" and t["c"].loc[0, "n.f"] == "A"
    assert t["c"].loc[0, "arr._count"] == 1 and t["c"].loc[0, "tags._count"] == 2
    assert t["c.arr"].loc[0, "x"] == 1
    assert t["c.arr.sub"].loc[0, "y"] == 2 and t["c.arr.sub"].loc[0, "_id"] == "abc"
    assert t["c.tags"]["value"].tolist() == ["a", "b"]
