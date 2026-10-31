"""MapVerity command line.

mapverity validate member.mapverity.yaml --html report.html --junit results.xml --gates gates.yaml
mapverity from-mapping mapping.csv --datasets datasets.yaml -o member.mapverity.yaml
mapverity mutate member.mapverity.yaml --dataset members --min-score 0.9
mapverity plan member.mapverity.yaml --history .mapverity/history.json --changed members.gender
mapverity risk train --out model.pkl && mapverity risk score src/transforms --model model.pkl
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import typer

from . import __version__
from .core.engine import load_data, validate
from .core.gates import GatePolicy, evaluate_gates
from .core.spec import DatasetSpec, Spec
from .reporting.formats import mutation_markdown, to_json, to_junit, to_markdown
from .validation.infer import InferOptions, infer_spec
from .validation.mapping import load_mapping, mapping_coverage, spec_from_mapping
from .validation.mutate import OPERATORS, mutation_test

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    help="MapVerity: field-level assurance for data pipelines.",
)
risk_app = typer.Typer(no_args_is_help=True, help="Explainable defect-risk model (needs mapverity[ai]).")
app.add_typer(risk_app, name="risk")


def _pairs(values: Optional[List[str]]) -> Dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise typer.BadParameter(f"expected NAME=PATH, got '{v}'")
        k, p = v.split("=", 1)
        out[k] = p
    return out


def _echo(text: str) -> None:
    with contextlib.suppress(BrokenPipeError):
        typer.echo(text)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mapverity {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show version."),
) -> None:
    """MapVerity: field-level assurance for data pipelines."""


@app.command("validate")
def cmd_validate(
    spec: Path = typer.Argument(..., exists=True, help="Spec file (*.mapverity.yaml)."),
    data: Optional[List[str]] = typer.Option(None, "--data", help="Override a dataset: NAME=PATH (repeatable)."),
    json_out: Optional[Path] = typer.Option(None, "--json"),
    junit: Optional[Path] = typer.Option(None, "--junit"),
    markdown: Optional[Path] = typer.Option(None, "--markdown"),
    html: Optional[Path] = typer.Option(None, "--html", help="Self-contained HTML report."),
    allure: Optional[Path] = typer.Option(None, "--allure", help="Directory for Allure result files."),
    gates: Optional[Path] = typer.Option(None, "--gates", exists=True, help="Quality-gate policy YAML."),
    mapping: Optional[Path] = typer.Option(
        None, "--mapping", exists=True, help="Mapping sheet, for the coverage gate."
    ),
    mutation_report: Optional[Path] = typer.Option(
        None, "--mutation-report", exists=True, help="JSON from `mapverity mutate --json`, for the mutation gate."
    ),
    risk: Optional[Path] = typer.Option(
        None, "--risk", exists=True, help="JSON {unit: risk} from `mapverity risk score`."
    ),
    history: Optional[Path] = typer.Option(
        None, "--history", help="Run-history JSON: read for ordering, updated after the run."
    ),
    prioritize: bool = typer.Option(
        False, "--prioritize", help="Run checks in ERAD-QA order (uses --history, --changed)."
    ),
    changed: Optional[List[str]] = typer.Option(
        None, "--changed", help="Changed data unit, e.g. members.gender (repeatable)."
    ),
    budget: Optional[float] = typer.Option(
        None, "--budget", help="Seconds; unrun blocking checks make the gate INCOMPLETE."
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop at the first blocking failure."),
) -> None:
    """Run a spec against data and decide PASS / WARN / BLOCK."""
    s = Spec.load(spec)
    overrides = _pairs(data)
    not_run: List[str] = []
    plan_dicts = None
    hist = None
    if history or prioritize or budget is not None or fail_fast:
        from .ai.prioritizer import ERADPrioritizer, run_plan
        from .ai.signals import RunHistory

        hist = RunHistory.load(history) if history else RunHistory()
        plan = (
            ERADPrioritizer().plan(s, hist, changed=set(changed or []))
            if prioritize
            else ERADPrioritizer(enabled=()).plan(s, hist)
        )
        if not prioritize:  # keep spec order
            order = {c.id: i for i, c in enumerate(s.checks)}
            plan.sort(key=lambda p: order[p.check])
        plan_dicts = [p.to_dict() for p in plan] if prioritize else None
        result, not_run = run_plan(s, plan, overrides, budget_seconds=budget, stop_on_failure=fail_fast)
        if history:
            hist.record_result(result)
            hist.save(history)
    else:
        result = validate(s, overrides)
    coverage = None
    if mapping:
        coverage = mapping_coverage(load_mapping(mapping), load_data(s, overrides)).to_dict()
    mutation = json.loads(mutation_report.read_text()) if mutation_report else None
    risk_map = json.loads(risk.read_text()) if risk else None
    decision = None
    if gates:
        decision = evaluate_gates(
            GatePolicy.load(gates), result=result, coverage=coverage, mutation=mutation, risk=risk_map, not_run=not_run
        )
    for path, render in ((json_out, to_json), (junit, to_junit), (markdown, to_markdown)):
        if path:
            path.write_text(render(result), encoding="utf-8")
    if html:
        from .reporting.html import render_html

        html.write_text(
            render_html(
                result,
                decision=decision.to_dict() if decision else None,
                coverage=coverage,
                mutation=mutation,
                plan=plan_dicts,
            ),
            encoding="utf-8",
        )
    if allure:
        from .reporting.allure import write_allure_results

        write_allure_results(result, allure)
    _echo(to_markdown(result))
    if not_run:
        _echo(f"{len(not_run)} check(s) not run (budget or fail-fast).")
    if decision:
        _echo(decision.summary())
        raise typer.Exit(decision.exit_code)
    raise typer.Exit(0 if result.passed and not not_run else 1)


@app.command("from-mapping")
def cmd_from_mapping(
    mapping: Path = typer.Argument(..., exists=True, help="Source-to-target mapping sheet (CSV or Excel)."),
    datasets: Optional[Path] = typer.Option(
        None, "--datasets", exists=True, help="YAML whose `datasets:` is copied in."
    ),
    name: str = typer.Option("data-model-mapping", "--name"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
) -> None:
    """Compile a mapping sheet into an executable spec."""
    base = Spec.load(datasets) if datasets else Spec()
    s = spec_from_mapping(mapping, name=name, datasets=base.datasets)
    if output:
        output.write_text(s.to_yaml(), encoding="utf-8")
        _echo(f"wrote {len(s.checks)} checks to {output}")
    else:
        _echo(s.to_yaml())


@app.command("from-openapi")
def cmd_from_openapi(
    document: Path = typer.Argument(..., exists=True, help="OpenAPI 3 / Swagger 2 document (YAML or JSON)."),
    schema: str = typer.Option(..., "--schema", help="Schema name under components.schemas."),
    dataset: str = typer.Option(..., "--dataset", help="Dataset name the API responses are loaded as."),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
) -> None:
    """Compile an API contract schema into field-level checks."""
    from .validation.openapi import spec_from_openapi

    s = spec_from_openapi(document, schema, dataset)
    text = s.to_yaml()
    if output:
        output.write_text(text, encoding="utf-8")
        _echo(f"wrote {len(s.checks)} checks to {output}")
    else:
        _echo(text)


@app.command("infer")
def cmd_infer(
    target: str = typer.Option(..., "--target"),
    source: Optional[str] = typer.Option(None, "--source"),
    target_format: Optional[str] = typer.Option(None, "--target-format"),
    source_format: Optional[str] = typer.Option(None, "--source-format"),
    key: Optional[str] = typer.Option(None, "--key"),
    name: str = typer.Option("inferred", "--name"),
    range_slack: float = typer.Option(0.10, "--range-slack"),
    output: Optional[Path] = typer.Option(None, "-o", "--output"),
) -> None:
    """Infer a draft spec from known-good output (and its source). Review before committing."""
    datasets = {"target": DatasetSpec(path=target, format=target_format)}
    if source:
        datasets["source"] = DatasetSpec(path=source, format=source_format)
    data = load_data(Spec(datasets=datasets, base_dir=Path.cwd()))
    s = infer_spec(
        data["target"], data.get("source"), key=key, name=name, options=InferOptions(range_slack=range_slack)
    )
    s.datasets = datasets
    if output:
        output.write_text(s.to_yaml(), encoding="utf-8")
        _echo(f"wrote {len(s.checks)} checks to {output}")
    else:
        _echo(s.to_yaml())


@app.command("coverage")
def cmd_coverage(
    spec: Path = typer.Argument(..., exists=True),
    mapping: Path = typer.Option(..., "--mapping", exists=True),
    data: Optional[List[str]] = typer.Option(None, "--data"),
) -> None:
    """Which data-model fields are value-validated, constraint-only, unmapped or missing."""
    s = Spec.load(spec)
    _echo(json.dumps(mapping_coverage(load_mapping(mapping), load_data(s, _pairs(data))).to_dict(), indent=2))


@app.command("diff-mapping")
def cmd_diff_mapping(
    old: Path = typer.Argument(..., exists=True), new: Path = typer.Argument(..., exists=True)
) -> None:
    """Fields added, removed or changed between two mapping versions (feeds --changed)."""
    from .ai.signals import diff_mappings

    _echo(json.dumps(diff_mappings(old, new), indent=2))


@app.command("mutate")
def cmd_mutate(
    spec: Path = typer.Argument(..., exists=True),
    dataset: str = typer.Option("target", "--dataset"),
    data: Optional[List[str]] = typer.Option(None, "--data"),
    operators: Optional[List[str]] = typer.Option(None, "--operator", help="Repeatable operator name (see docs)"),
    rate: float = typer.Option(0.05, "--rate"),
    seed: int = typer.Option(0, "--seed"),
    json_out: Optional[Path] = typer.Option(None, "--json"),
    min_score: Optional[float] = typer.Option(None, "--min-score", help="Exit 1 below this score (CI gate)."),
) -> None:
    """Score a spec by injecting realistic ETL faults into the output."""
    s = Spec.load(spec)
    for op in operators or []:
        if op not in OPERATORS:
            raise typer.BadParameter(f"unknown operator '{op}'")
    rep = mutation_test(
        s, load_data(s, _pairs(data)), dataset=dataset, operators=operators or None, rate=rate, seed=seed
    ).to_dict()
    if json_out:
        json_out.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    _echo(mutation_markdown(rep))
    raise typer.Exit(0 if (min_score is None or (rep["score"] or 0) >= min_score) else 1)


@app.command("plan")
def cmd_plan(
    spec: Path = typer.Argument(..., exists=True),
    history: Optional[Path] = typer.Option(None, "--history"),
    changed: Optional[List[str]] = typer.Option(None, "--changed"),
    risk: Optional[Path] = typer.Option(None, "--risk", exists=True, help="JSON {data_unit: probability}."),
    baseline: Optional[Path] = typer.Option(None, "--baseline", exists=True, help="Profile baseline JSON for drift."),
    data: Optional[List[str]] = typer.Option(None, "--data"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Show the ERAD-QA execution order with the reason for each position."""
    from .ai.prioritizer import ERADPrioritizer
    from .ai.signals import ProfileProbe, RunHistory

    s = Spec.load(spec)
    hist = RunHistory.load(history) if history else None
    drift = ProfileProbe.load(baseline).drift(load_data(s, _pairs(data))) if baseline else None
    risk_map = json.loads(risk.read_text()) if risk else None
    plan = ERADPrioritizer().plan(s, hist, risk_map, set(changed or []), drift)
    _echo("| # | check | priority | top reasons |\n|---:|---|---:|---|")
    for i, p in enumerate(plan[:limit], 1):
        why = ", ".join(
            f"{k} {v:+.2f}" for k, v in sorted(p.contributions.items(), key=lambda kv: -abs(kv[1]))[:3] if v
        )
        _echo(f"| {i} | `{p.check}` | {p.priority:.3f} | {why or 'baseline'} |")


@app.command("baseline")
def cmd_baseline(
    spec: Path = typer.Argument(..., exists=True),
    output: Path = typer.Option(..., "-o", "--output"),
    data: Optional[List[str]] = typer.Option(None, "--data"),
    tables: Optional[List[str]] = typer.Option(None, "--table"),
) -> None:
    """Save column profiles of known-good data, for drift signals in `plan`."""
    from .ai.signals import ProfileProbe

    s = Spec.load(spec)
    ProfileProbe.from_data(load_data(s, _pairs(data)), tables or None).save(output)
    _echo(f"wrote profile baseline to {output}")


@risk_app.command("train")
def cmd_risk_train(
    out: Path = typer.Option(Path("defect-risk.pkl"), "--out"),
    projects: Optional[List[str]] = typer.Option(None, "--project", help="NASA MDP projects (default: all 12)."),
    estimator: str = typer.Option("xgboost", "--estimator"),
    smote: bool = typer.Option(False, "--smote/--no-smote"),
) -> None:
    """Train the portable defect-risk model on public NASA MDP data (downloaded, checksum-verified)."""
    import pandas as pd

    from .ai.datasets import LABEL, PORTABLE_FEATURES, load_nasa_mdp
    from .ai.predictor import DefectRiskModel

    frames = load_nasa_mdp(projects or None)
    df = pd.concat(frames.values(), ignore_index=True)
    m = DefectRiskModel(estimator=estimator, smote=smote, features=PORTABLE_FEATURES)
    m.fit(df[PORTABLE_FEATURES], df[LABEL], sources=sorted(frames))
    digest = m.save(out)
    _echo(f"trained on {len(df)} modules from {len(frames)} projects; saved {out} (sha256 {digest[:12]})")


@risk_app.command("score")
def cmd_risk_score(
    code: List[Path] = typer.Argument(..., exists=True, help="Python files or directories of transformation code."),
    model: Path = typer.Option(..., "--model", exists=True),
    ownership: Optional[Path] = typer.Option(None, "--ownership", exists=True, help="YAML: code unit -> data units."),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Write {data_unit or code_unit: risk} JSON."),
    top: int = typer.Option(10, "--top"),
) -> None:
    """Score functions in transformation code and explain the top drivers."""
    from .ai.codemetrics import metrics_frame
    from .ai.explain import top_reasons
    from .ai.predictor import DefectRiskModel
    from .ai.signals import data_unit_risk, load_ownership

    m = DefectRiskModel.load(model)
    df = metrics_frame(list(code))
    if df.empty:
        _echo("no functions found")
        raise typer.Exit(1)
    p = m.predict_proba(df[m.features])
    reasons = top_reasons(m.explain(df[m.features]), 3)
    df = df.assign(risk=p, reasons=[", ".join(r) for r in reasons]).sort_values("risk", ascending=False)
    _echo("| unit | risk | top drivers |\n|---|---:|---|")
    for r in df.head(top).itertuples():
        _echo(f"| `{r.unit}` | {r.risk:.2f} | {r.reasons} |")
    code_risk = dict(zip(df["unit"], df["risk"].astype(float)))
    result = data_unit_risk(code_risk, load_ownership(ownership)) if ownership else code_risk
    if output:
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")


@app.command("datasets")
def cmd_datasets(fetch: bool = typer.Option(True, "--fetch/--list")) -> None:
    """Download and checksum-verify the public NASA MDP defect datasets (or list them)."""
    from .ai.datasets import NASA_MDP, fetch_nasa_mdp

    if not fetch:
        for k, v in NASA_MDP.items():
            _echo(f"{k}  sha256:{v}")
        return
    for k, p in fetch_nasa_mdp().items():
        _echo(f"{k}: {p}")


def main() -> None:
    import os

    try:
        app()
    except BrokenPipeError:  # pragma: no cover
        sys.exit(0)
    except (FileNotFoundError, KeyError, ValueError, ImportError, OSError) as exc:
        if os.environ.get("MAPVERITY_DEBUG"):
            raise
        typer.echo(f"mapverity: error: {type(exc).__name__}: {exc}  (set MAPVERITY_DEBUG=1 for a traceback)", err=True)
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()
