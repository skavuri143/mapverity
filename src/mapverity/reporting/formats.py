"""Render validation results as JSON, JUnit XML (for CI dashboards) and Markdown."""

from __future__ import annotations

import json
from typing import Any, Dict
from xml.sax.saxutils import escape, quoteattr

from ..core.engine import ValidationResult


def to_json(result: ValidationResult, indent: int = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent, default=str)


def to_junit(result: ValidationResult) -> str:
    s = result.summary()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuites name={quoteattr(result.spec_name)} tests="{s["total"]}" failures="{s["failed"]}" '
        f'errors="{s["error"]}" time="{result.seconds:.3f}">',
        f'  <testsuite name={quoteattr(result.spec_name)} tests="{s["total"]}" failures="{s["failed"]}" '
        f'errors="{s["error"]}" time="{result.seconds:.3f}">',
    ]
    for r in result.results:
        classname = f"{result.spec_name}.{r.check.dataset or 'data'}"
        lines.append(
            f'    <testcase classname={quoteattr(classname)} name={quoteattr(r.check.id)} time="{r.seconds:.4f}">'
        )
        if r.status == "failed":
            detail = json.dumps(r.outcome.sample, default=str)
            lines.append(f"      <failure message={quoteattr(r.outcome.message)}>{escape(detail)}</failure>")
        elif r.status == "error":
            lines.append(f"      <error message={quoteattr(r.error or '')}/>")
        lines.append("    </testcase>")
    lines += ["  </testsuite>", "</testsuites>"]
    return "\n".join(lines) + "\n"


def to_markdown(result: ValidationResult) -> str:
    s = result.summary()
    status = "PASSED" if result.passed else "FAILED"
    out = [
        f"## mapverity: {result.spec_name} — {status}",
        "",
        f"{s['passed']} passed · {s['failed']} failed · {s['error']} errors · {result.seconds:.2f}s",
        "",
    ]
    bad = [r for r in result.results if not r.passed]
    if bad:
        out += ["| Check | Status | Failed | Detail |", "|---|---|---:|---|"]
        for r in bad:
            msg = (r.error or r.outcome.message).replace("|", "\\|")[:200]
            out.append(f"| `{r.check.id}` | {r.status} | {r.outcome.failed} | {msg} |")
    return "\n".join(out) + "\n"


def mutation_markdown(report: Dict[str, Any]) -> str:
    out = [
        f"## Mutation score: {report['score']:.1%} ({report['killed']}/{report['mutants']} mutants killed)",
        "",
        "| Fault class | Mutants | Killed | Score |",
        "|---|---:|---:|---:|",
    ]
    for k, v in report["by_fault_class"].items():
        out.append(f"| {k} | {v['mutants']} | {v['killed']} | {v['score']:.0%} |")
    if report["survivors"]:
        out += ["", "**Surviving mutants (blind spots):**", ""]
        for r in report["survivors"][:25]:
            out.append(f"- `{r['operator']}` on `{r['column'] or '(rows)'}`")
    return "\n".join(out) + "\n"
