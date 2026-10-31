import sqlite3

import pandas as pd
import pytest

from mapverity import Spec, spec_from_openapi, validate
from mapverity.connectors import read_any
from mapverity.validation.fixedwidth import load_layout, read_fixed_width, read_fixed_width_tables

LAYOUT = [
    {"name": "rec", "start": 1, "length": 1},
    {"name": "id", "start": 2, "length": 4, "record_type": "D"},
    {"name": "amt", "start": 6, "length": 7, "type": "number", "implied_decimals": 2, "record_type": "D"},
    {"name": "dt", "start": 13, "length": 8, "type": "date", "date_format": "%Y%m%d", "record_type": "D"},
    {"name": "count", "start": 2, "length": 3, "type": "integer", "record_type": "T"},
]


def test_fixed_width_multi_record_signed_implied_decimals():
    text = "DA001001250 20261030\nDA002000075-20261031\nT002\n"
    tables = read_fixed_width_tables(text.encode(), LAYOUT, type_field="rec")
    d = tables["D"]
    assert d["id"].tolist() == ["A001", "A002"]
    assert d["amt"].tolist() == [12.50, -0.75]
    assert d["dt"].tolist() == ["2026-10-30", "2026-10-31"]
    assert tables["T"]["count"].tolist() == [2]


def test_fixed_width_single_record_and_strict(tmp_path):
    layout = tmp_path / "l.csv"
    layout.write_text("name,start,length,type\nid,1,3,string\nn,4,2,integer\n")
    assert len(load_layout(layout)) == 2
    df = read_fixed_width(b"abc12\nxyz07\n", layout)
    assert df["n"].tolist() == [12, 7]
    with pytest.raises(ValueError):
        read_fixed_width(b"abc123\n", layout, strict_length=True)


def test_sql_connector_sqlite(tmp_path):
    pytest.importorskip("sqlalchemy")
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("create table claims (id text, amt real)")
    con.executemany("insert into claims values (?, ?)", [("a", 1.5), ("b", 2.0)])
    con.commit()
    con.close()
    out = read_any("claims", f"sqlite:///{db}", None, {"table": "claims"})
    assert out["claims"]["amt"].sum() == 3.5
    out = read_any(
        "q", f"sqlite:///{db}", "sql", {"query": "select id from claims where amt > :m", "params": {"m": 1.8}}
    )
    assert out["q"]["id"].tolist() == ["b"]
    with pytest.raises(ValueError):
        read_any("x", f"sqlite:///{db}", "sql", {"table": "claims; drop table claims"})


def test_rest_connector_paginates_and_flattens():
    httpx = pytest.importorskip("httpx")
    pages = {
        "/v1/m?p=1": {"data": [{"id": "1", "name": {"first": "A"}, "plans": [{"c": "X"}]}], "next": "/v1/m?p=2"},
        "/v1/m?p=2": {"data": [{"id": "2", "name": {"first": "B"}, "plans": []}], "next": None},
    }

    def handler(request):
        assert request.headers["authorization"] == "Bearer t0k"
        return httpx.Response(200, json=pages[f"{request.url.path}?{request.url.query.decode()}"])

    from mapverity.connectors.rest import fetch_rest
    from mapverity.validation.documents import flatten_documents

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test")
    recs = fetch_rest(
        "https://api.test/v1/m?p=1",
        {"records": "data", "next": "next", "headers": {"Authorization": "Bearer t0k"}},
        client=client,
    )
    t = flatten_documents(recs, "members", key="id")
    assert t["members"]["name.first"].tolist() == ["A", "B"]
    assert t["members"]["plans._count"].tolist() == [1, 0]


OPENAPI = {
    "openapi": "3.0.3",
    "components": {
        "schemas": {
            "Member": {
                "type": "object",
                "required": ["memberId", "status", "name"],
                "properties": {
                    "memberId": {"type": "string", "pattern": "^M\\d{4}$", "maxLength": 5},
                    "status": {"type": "string", "enum": ["ACTIVE", "PENDING"]},
                    "birthDate": {"type": "string", "format": "date"},
                    "householdSize": {"type": "integer", "minimum": 1, "maximum": 20},
                    "name": {"$ref": "#/components/schemas/Name"},
                    "plans": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}},
                    },
                },
            },
            "Name": {
                "type": "object",
                "required": ["first"],
                "properties": {"first": {"type": "string", "minLength": 1}},
            },
        }
    },
}


def test_openapi_contract_compiles_and_catches_violations():
    spec = spec_from_openapi(OPENAPI, "Member", "members")
    ids = {c.id for c in spec.checks}
    assert {
        "api:members:memberId:required",
        "api:members:status:enum",
        "api:members:memberId:pattern",
        "api:members:householdSize:range",
        "api:members:name.first:required",
        "api:members:plans:minItems",
        "api:members.plans:code:required",
    } <= ids
    good = pd.DataFrame(
        {
            "memberId": ["M0001"],
            "status": ["ACTIVE"],
            "birthDate": ["1990-01-01"],
            "householdSize": [3],
            "name.first": ["Ana"],
            "plans._count": [1],
        }
    )
    plans = pd.DataFrame({"memberId": ["M0001"], "code": ["X"]})
    assert validate(spec, {"members": good, "members.plans": plans}).passed
    bad = good.assign(status=["GONE"], householdSize=[0], memberId=["X1"])
    failed = {r.check.id for r in validate(spec, {"members": bad, "members.plans": plans}).failures}
    assert {"api:members:status:enum", "api:members:householdSize:range", "api:members:memberId:pattern"} <= failed


def test_openapi_unknown_schema():
    with pytest.raises(KeyError):
        spec_from_openapi(OPENAPI, "Nope", "x")


def test_control_total_check():
    from mapverity import CheckSpec

    det = pd.DataFrame({"paid": [1.0, 2.5]})
    trl = pd.DataFrame({"n": [2], "total": [3.5]})
    checks = [
        CheckSpec("control_total", dataset="d", params={"control_dataset": "t", "control_column": "n"}),
        CheckSpec(
            "control_total",
            dataset="d",
            column="paid",
            params={"control_dataset": "t", "control_column": "total", "agg": "sum"},
        ),
    ]
    assert validate(Spec(checks=checks), {"d": det, "t": trl}).passed
    r = validate(Spec(checks=checks), {"d": det.iloc[:1], "t": trl})
    assert len(r.failures) == 2
