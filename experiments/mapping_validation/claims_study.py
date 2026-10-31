"""Insurance-claims example: seeded defects and data mutation (reproduces the README claims numbers).

python experiments/mapping_validation/claims_study.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "insurance_claims"
sys.path.insert(0, str(EX))

import generate_data as G  # noqa: E402
import transform as T  # noqa: E402

from mapverity import Spec, flatten_documents, load_data, mutation_test, spec_from_mapping, validate  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    n_claims, n_lines, _ = G.generate(800, 11, tmp / "landing", tmp / "reference")
    base = Spec.load(EX / "datasets.yaml")
    base.datasets["claims_file"].path = str(tmp / "landing")
    base.datasets["policies"].path = str(tmp / "reference" / "policies.csv")
    del base.datasets["claims"]
    spec = spec_from_mapping(EX / "mapping" / "claims_mapping.csv", datasets=base.datasets)
    spec.base_dir = EX
    src = load_data(spec, {})
    tables = T.read_sources(tmp / "landing")
    good = flatten_documents(T.transform(tables), "claims", key="claimId")
    clean = validate(spec, {**src, **good})
    defects = {}
    for d in T.DEFECTS:
        bad = flatten_documents(T.transform(tables, {d}), "claims", key="claimId")
        r = validate(spec, {**src, **bad})
        defects[d] = [x.check.id for x in r.failures]
    mutation = {}
    for table in ("claims", "claims.lines"):
        rep = mutation_test(spec, {**src, **good}, dataset=table, rate=0.05, seed=1)
        mutation[table] = {"mutants": rep.total, "killed": rep.killed, "score": rep.score}
    summary = {
        "claims": n_claims,
        "service_lines": n_lines,
        "checks": len(spec.checks),
        "clean_run_passed": clean.passed,
        "defects_detected": sum(bool(v) for v in defects.values()),
        "defects_total": len(defects),
        "detecting_checks": defects,
        "mutation": mutation,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "claims_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "detecting_checks"}, indent=1))


if __name__ == "__main__":
    main()
