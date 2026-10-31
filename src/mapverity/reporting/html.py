"""Self-contained HTML report (no external assets, safe to attach to a CI run or email)."""

from __future__ import annotations

import datetime as _dt
import json
from html import escape
from typing import Any, Dict, List, Optional

from ..core.engine import ValidationResult

_CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700;--chip:#f6f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#30363d;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--chip:#161b22}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.PASS,.passed{color:var(--ok)}.BLOCK,.failed{color:var(--bad)}.WARN,.INCOMPLETE,.error{color:var(--warn)}
table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;font-size:13px;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}
code,pre{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}pre{background:var(--chip);border:1px solid var(--line);border-radius:6px;padding:8px;overflow:auto;max-height:240px;margin:4px 0}
details summary{cursor:pointer}.bar{display:inline-block;height:8px;background:currentColor;border-radius:2px;vertical-align:middle}
@media (max-width:640px){td,th{padding:4px}.hide-sm{display:none}}
"""


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{x:.1%}"


def render_html(
    result: ValidationResult,
    title: Optional[str] = None,
    decision: Optional[Dict[str, Any]] = None,
    coverage: Optional[Dict[str, Any]] = None,
    mutation: Optional[Dict[str, Any]] = None,
    plan: Optional[List[Dict[str, Any]]] = None,
) -> str:
    s = result.summary()
    title = title or f"MapVerity report: {result.spec_name}"
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(title)}</h1><div class='muted'>Generated {now} · {result.seconds:.2f}s</div>",
    ]
    overall = decision["outcome"] if decision else ("PASS" if result.passed else "BLOCK")
    out.append(
        "<div class='chips'>"
        f"<span class='chip'>Decision <b class='{overall}'>{overall}</b></span>"
        f"<span class='chip'><b class='passed'>{s['passed']}</b> passed</span>"
        f"<span class='chip'><b class='failed'>{s['failed']}</b> failed</span>"
        f"<span class='chip'><b class='error'>{s['error']}</b> errors</span>"
    )
    if coverage:
        out.append(f"<span class='chip'>Value coverage <b>{_pct(coverage.get('value_coverage'))}</b></span>")
    if mutation:
        out.append(f"<span class='chip'>Mutation score <b>{_pct(mutation.get('score'))}</b></span>")
    out.append("</div>")

    if decision:
        out.append("<h2>Quality gate</h2><table><tr><th>Gate</th><th>Outcome</th><th>Detail</th></tr>")
        for f in decision["findings"]:
            out.append(
                f"<tr><td>{escape(f['gate'])}</td><td class='{f['outcome']}'>{f['outcome']}</td><td>{escape(f['detail'])}</td></tr>"
            )
        out.append("</table>")

    failures = [r for r in result.results if not r.passed]
    out.append(f"<h2>Failures ({len(failures)})</h2>")
    if not failures:
        out.append("<p class='muted'>None.</p>")
    else:
        out.append(
            "<table><tr><th>Check</th><th>Status</th><th class='num'>Failed</th><th>Detail and examples</th></tr>"
        )
        for r in failures:
            msg = r.error or r.outcome.message
            sample = json.dumps(r.outcome.sample, indent=1, default=str) if r.outcome.sample else ""
            out.append(
                f"<tr><td><code>{escape(r.check.id)}</code></td><td class='{r.status}'>{r.status}</td>"
                f"<td class='num'>{r.outcome.failed:,}</td><td>{escape(msg)}"
                + (f"<details><summary>examples</summary><pre>{escape(sample)}</pre></details>" if sample else "")
                + "</td></tr>"
            )
        out.append("</table>")

    if plan:
        out.append(
            "<h2>Execution order (ERAD-QA)</h2><table><tr><th class='num'>#</th><th>Check</th>"
            "<th class='num'>Priority</th><th class='hide-sm'>Why</th></tr>"
        )
        for i, p in enumerate(plan, 1):
            top = sorted(p.get("contributions", {}).items(), key=lambda kv: -abs(kv[1]))[:3]
            why = ", ".join(f"{k} {v:+.2f}" for k, v in top if abs(v) >= 0.005) or "baseline"
            out.append(
                f"<tr><td class='num'>{i}</td><td><code>{escape(p['check'])}</code></td>"
                f"<td class='num'>{p['priority']:.3f}</td><td class='hide-sm muted'>{escape(why)}</td></tr>"
            )
        out.append("</table>")

    if coverage:
        out.append("<h2>Mapping coverage</h2><table>")
        for k in ("fields_in_mapping", "value_validated"):
            out.append(f"<tr><th>{escape(k.replace('_', ' '))}</th><td>{coverage.get(k)}</td></tr>")
        for k in ("constraint_only", "not_validated", "unmapped_in_data", "missing_in_data"):
            v = coverage.get(k) or []
            out.append(f"<tr><th>{escape(k.replace('_', ' '))}</th><td>{escape(', '.join(v)) if v else '–'}</td></tr>")
        out.append("</table>")

    if mutation:
        out.append(
            "<h2>Mutation testing</h2><table><tr><th>Fault class</th><th class='num'>Mutants</th><th class='num'>Killed</th><th>Score</th></tr>"
        )
        for k, v in mutation.get("by_fault_class", {}).items():
            out.append(
                f"<tr><td>{escape(k)}</td><td class='num'>{v['mutants']}</td><td class='num'>{v['killed']}</td>"
                f"<td><span class='bar' style='width:{v['score'] * 120:.0f}px'></span> {v['score']:.0%}</td></tr>"
            )
        out.append("</table>")
        if mutation.get("survivors"):
            out.append("<details><summary>Surviving mutants (blind spots)</summary><ul>")
            for sv in mutation["survivors"][:50]:
                out.append(
                    f"<li><code>{escape(sv['operator'])}</code> on <code>{escape(str(sv['column'] or '(rows)'))}</code></li>"
                )
            out.append("</ul></details>")

    out.append(
        f"<h2>All checks ({len(result.results)})</h2><details><summary>show</summary><table>"
        "<tr><th>Check</th><th>Status</th><th class='num'>Rows</th><th class='num hide-sm'>Seconds</th></tr>"
    )
    for r in result.results:
        out.append(
            f"<tr><td><code>{escape(r.check.id)}</code></td><td class='{r.status}'>{r.status}</td>"
            f"<td class='num'>{r.outcome.total:,}</td><td class='num hide-sm'>{r.seconds:.3f}</td></tr>"
        )
    out.append("</table></details></main></body></html>")
    return "".join(out)
