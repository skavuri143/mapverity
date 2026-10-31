"""Reference transformation: claims file -> claim JSON documents with nested service lines.

This stands in for the partner-ingestion job (Glue, Databricks, an ETL tool...). MapVerity only
sees its input files and output documents. `defects` switches on seeded bugs for the example.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Dict, List, Set

from mapverity.connectors import read_any

HERE = Path(__file__).parent
CLAIM_TYPE = {"P": "PROFESSIONAL", "I": "INSTITUTIONAL", "D": "DENTAL"}
STATUS = {"01": "PAID", "02": "DENIED", "04": "PENDED", "22": "REVERSED"}

DEFECTS = {
    "C01_line_amount_scale_lost": "line billed amount ignores the 2 implied decimals",
    "C02_reversal_mapped_paid": "status 22 (reversed) translated to PAID",
    "C03_icd_dot_misplaced": "ICD-10 dot inserted after the 4th character",
    "C04_zero_paid_lines_dropped": "service lines with zero paid are dropped",
    "C05_reversal_sign_lost": "reversal amounts lose their negative sign",
    "C06_service_dates_swapped": "service from/to dates swapped",
    "C07_pos_leading_zero_lost": "place of service '02' becomes '2'",
    "C08_reversals_not_loaded": "reversed claims filtered out",
    "C09_patient_resp_uses_billed": "patient responsibility computed from billed, not allowed",
    "C10_policy_suffix_appended": "policy number gets a '-01' member suffix",
}


def text(v) -> str:
    """Blank fixed-width fields arrive as null (None/NaN)."""
    return "" if v is None or v != v else str(v)


def iso(v) -> str | None:
    v = text(v).strip()
    return dt.datetime.strptime(v, "%Y%m%d").strftime("%Y-%m-%d") if v else None


def icd10(dx: str, d: Set[str]) -> str:
    dx = text(dx).strip()
    cut = 4 if "C03_icd_dot_misplaced" in d else 3
    return f"{dx[:cut]}.{dx[cut:]}" if len(dx) > cut else dx


def money(v, d: Set[str]) -> float:
    v = 0.0 if v is None or v != v else float(v)
    return abs(v) if "C05_reversal_sign_lost" in d else v


def transform(tables: Dict[str, object], defects: Set[str] = frozenset()) -> List[Dict]:
    d = set(defects)
    clm, lin = tables["claims_file.CLM"], tables["claims_file.LIN"]
    lines: Dict[str, List] = {}
    for r in lin.itertuples(index=False):
        lines.setdefault(r.claim_id.strip(), []).append(r)
    status = dict(STATUS, **({"22": "PAID"} if "C02_reversal_mapped_paid" in d else {}))
    docs = []
    for r in clm.itertuples(index=False):
        if "C08_reversals_not_loaded" in d and r.status_cd == "22":
            continue
        cid = r.claim_id.strip()
        start, end = iso(r.svc_from), iso(r.svc_to)
        if "C06_service_dates_swapped" in d:
            start, end = end, start
        allowed, paid = money(r.total_allowed, d), money(r.total_paid, d)
        resp_base = money(r.total_billed, d) if "C09_patient_resp_uses_billed" in d else allowed
        out_lines = []
        for ln in lines.get(cid, []):
            if "C04_zero_paid_lines_dropped" in d and money(ln.line_paid, set()) == 0:
                continue
            pos = ln.pos.strip()
            out_lines.append(
                {
                    "lineNo": int(ln.line_no),
                    "procedureCode": ln.proc_cd.strip().upper(),
                    "modifier": text(ln.modifier).strip() or None,
                    "placeOfService": str(int(pos)) if "C07_pos_leading_zero_lost" in d else pos.zfill(2),
                    "units": int(ln.units),
                    "billedAmount": money(ln.line_billed, d) * (100 if "C01_line_amount_scale_lost" in d else 1),
                    "paidAmount": money(ln.line_paid, d),
                }
            )
        docs.append(
            {
                "_id": cid,
                "claimId": cid,
                "policyNumber": r.policy_no.strip() + ("-01" if "C10_policy_suffix_appended" in d else ""),
                "claimType": CLAIM_TYPE.get(r.claim_type),
                "status": status.get(r.status_cd),
                "serviceFrom": start,
                "serviceTo": end,
                "providerNpi": r.npi.strip(),
                "diagnosisCode": icd10(r.dx1, d),
                "billedAmount": money(r.total_billed, d),
                "allowedAmount": allowed,
                "paidAmount": paid,
                "patientResponsibility": round(resp_base - paid, 2),
                "lines": out_lines,
            }
        )
    return docs


def read_sources(landing: Path) -> Dict[str, object]:
    return read_any(
        "claims_file",
        str(landing),
        "fixed_width",
        {"layout": str(HERE / "layouts" / "CLAIMS-FILE.layout.csv"), "multi_record": True, "type_field": "rec_type"},
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--landing", default=str(HERE / "landing"))
    p.add_argument("--out", default=str(HERE / "output" / "claims.jsonl"))
    p.add_argument("--defect", action="append", default=[], choices=sorted(DEFECTS))
    a = p.parse_args()
    docs = transform(read_sources(Path(a.landing)), set(a.defect))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("".join(json.dumps(x) + "\n" for x in docs), encoding="utf-8")
    print(f"wrote {len(docs)} claim documents to {a.out}")
