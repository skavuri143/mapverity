import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mapverity import Spec, flatten_documents, load_data, spec_from_mapping, validate
from mapverity.cli import app

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "examples" / "insurance_claims"
MEMBER = ROOT / "examples" / "member_eligibility"
runner = CliRunner()


@pytest.fixture
def member_workspace(tmp_path, monkeypatch):
    for mod in ("generate_data", "transform"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(MEMBER))
    import generate_data
    import transform as T

    for name in ("copybooks", "mapping"):
        (tmp_path / name).mkdir()
        for f in (MEMBER / name).iterdir():
            (tmp_path / name / f.name).write_bytes(f.read_bytes())
    for f in ("datasets.yaml", "gates.yaml", "ownership.yaml", "transform.py"):
        (tmp_path / f).write_bytes((MEMBER / f).read_bytes())
    generate_data.generate(300, 3, tmp_path / "landing")
    master, elig = T.read_sources(tmp_path / "landing")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "members.jsonl").write_text("".join(json.dumps(d) + "\n" for d in T.transform(master, elig)))
    monkeypatch.chdir(tmp_path)
    sys.path.remove(str(MEMBER))
    return tmp_path


def test_cli_full_member_flow(member_workspace):
    r = runner.invoke(
        app,
        [
            "from-mapping",
            "mapping/member_data_model_mapping.csv",
            "--datasets",
            "datasets.yaml",
            "-o",
            "member.mapverity.yaml",
        ],
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app, ["mutate", "member.mapverity.yaml", "--dataset", "members", "--json", "mut.json", "--min-score", "0.9"]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app,
        [
            "validate",
            "member.mapverity.yaml",
            "--mapping",
            "mapping/member_data_model_mapping.csv",
            "--gates",
            "gates.yaml",
            "--mutation-report",
            "mut.json",
            "--html",
            "report.html",
            "--junit",
            "junit.xml",
            "--allure",
            "allure",
            "--history",
            ".mapverity/history.json",
            "--prioritize",
            "--changed",
            "members.gender",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "Quality gate: PASS" in r.output
    assert "Execution order" in Path("report.html").read_text()
    hist = json.loads(Path(".mapverity/history.json").read_text())
    assert len(hist["runs"]) == 49
    r = runner.invoke(
        app,
        [
            "plan",
            "member.mapverity.yaml",
            "--history",
            ".mapverity/history.json",
            "--changed",
            "members.gender",
            "--limit",
            "3",
        ],
    )
    assert r.exit_code == 0 and "members:gender" in r.output.splitlines()[2]
    r = runner.invoke(app, ["baseline", "member.mapverity.yaml", "-o", "baseline.json", "--table", "members"])
    assert r.exit_code == 0 and "members" in json.loads(Path("baseline.json").read_text())


def test_cli_budget_makes_gate_incomplete(member_workspace):
    runner.invoke(
        app,
        [
            "from-mapping",
            "mapping/member_data_model_mapping.csv",
            "--datasets",
            "datasets.yaml",
            "-o",
            "member.mapverity.yaml",
        ],
    )
    Path("g.yaml").write_text("gates: {}\n")
    r = runner.invoke(app, ["validate", "member.mapverity.yaml", "--gates", "g.yaml", "--budget", "0"])
    assert r.exit_code == 1 and "INCOMPLETE" in r.output


def test_cli_diff_mapping_and_openapi(tmp_path):
    old = MEMBER / "mapping" / "member_data_model_mapping.csv"
    new = tmp_path / "new.csv"
    new.write_text(old.read_text().replace("lookup: M=MALE, F=FEMALE, *=UNKNOWN", "lookup: M=MALE, F=FEMALE"))
    r = runner.invoke(app, ["diff-mapping", str(old), str(new)])
    assert json.loads(r.output)["changed"] == ["members.gender"]
    api = tmp_path / "api.yaml"
    api.write_text(
        "openapi: 3.0.3\ncomponents:\n  schemas:\n    M:\n      type: object\n      required: [id]\n"
        "      properties:\n        id: {type: string}\n"
    )
    r = runner.invoke(app, ["from-openapi", str(api), "--schema", "M", "--dataset", "m"])
    assert r.exit_code == 0 and "api:m:id:required" in r.output


def test_insurance_claims_example_catches_every_seeded_defect(tmp_path):
    for mod in ("generate_data", "transform"):  # the member example uses the same module names
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(CLAIMS))
    try:
        import generate_data as G
        import transform as T
    finally:
        sys.path.remove(str(CLAIMS))
    for mod in ("generate_data", "transform"):
        sys.modules.pop(mod, None)
    G.generate(200, 5, tmp_path / "landing", tmp_path / "reference")
    tables = T.read_sources(tmp_path / "landing")
    base = Spec.load(CLAIMS / "datasets.yaml")
    base.datasets["claims_file"].path = str(tmp_path / "landing")
    base.datasets["policies"].path = str(tmp_path / "reference" / "policies.csv")
    del base.datasets["claims"]
    base.base_dir = CLAIMS
    spec = spec_from_mapping(CLAIMS / "mapping" / "claims_mapping.csv", datasets=base.datasets)
    spec.base_dir = CLAIMS
    src = load_data(spec, {})
    good = flatten_documents(T.transform(tables), "claims", key="claimId")
    assert validate(spec, {**src, **good}).passed
    controls = Spec.load(CLAIMS / "file_controls.mapverity.yaml")
    controls.datasets["claims_file"].path = str(tmp_path / "landing")
    assert validate(controls).passed
    for d in T.DEFECTS:
        bad = flatten_documents(T.transform(tables, {d}), "claims", key="claimId")
        assert not validate(spec, {**src, **bad}).passed, d
