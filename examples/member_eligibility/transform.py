"""Reference implementation of the member Glue job's transformation, in plain pandas.

`glue_job.py` is the version that runs in AWS Glue. This module implements the same data-model
mapping locally so the example (and the experiments in /experiments) can run without AWS.
`defects` switches on realistic, seeded transformation bugs used to evaluate the test suites.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Dict, List, Set

import pandas as pd

from mapverity.connectors import read_table

HERE = Path(__file__).parent
GENDER = {"M": "MALE", "F": "FEMALE"}
STATUS = {"A": "ACTIVE", "P": "PENDING"}
PLAN = {
    "FFS": "FEE_FOR_SERVICE",
    "MCO1": "HEALTH_PLAN_A",
    "MCO2": "HEALTH_PLAN_B",
    "MCO3": "HEALTH_PLAN_C",
    "MCO4": "HEALTH_PLAN_D",
    "MCO5": "HEALTH_PLAN_E",
}

# Seeded defects: each one is a plausible bug in a Glue/PySpark mapping job.
DEFECTS = {
    "D01_filter_includes_terminated": "filter forgets to exclude status T",
    "D02_filter_drops_pending": "filter keeps only status A",
    "D03_name_not_trimmed": "reader keeps PIC X(20) padding (Cobrix string_trimming_policy=none)",
    "D04_name_case_not_normalised": "first name keeps mainframe casing",
    "D05_dob_month_day_swapped": "birthDate parsed as YYYYDDMM",
    "D06_gender_unknown_mapped_null": "lookup default missing -> null instead of UNKNOWN",
    "D07_comp3_scale_lost": "COMP-3 implied decimals ignored (income x100)",
    "D08_fpl_uses_annual_income_twice": "FPL computed from income*12*12",
    "D09_fpl_hh_off_by_one": "FPL guideline uses household size instead of size-1",
    "D10_county_leading_zero_lost": "county code cast to int, '07' -> '7'",
    "D11_phone_format_wrong": "phone emitted without dashes",
    "D12_status_lookup_swapped": "A->PENDING and P->ACTIVE",
    "D13_segments_inner_join_drops_last": "only the first eligibility segment is nested",
    "D14_segment_end_date_shifted": "end date off by one day (timezone conversion)",
    "D15_open_end_date_defaulted": "open-ended segment gets 9999-12-31 instead of null",
    "D16_plan_code_untranslated": "plan code copied without translation",
    "D17_aid_category_not_uppercased": "aidCategory case not normalised",
    "D18_duplicate_documents": "upsert key wrong -> members written twice",
    "D19_source_system_wrong": "lineage constant 'LEGACY ' with trailing space",
    "D20_household_size_from_wrong_field": "householdSize read from county code",
}


def _date(v: str, fmt: str = "%Y%m%d"):
    v = str(v).strip()
    if not v or v == "0":
        return None
    try:
        return dt.datetime.strptime(v, fmt).strftime("%Y-%m-%d")
    except ValueError:  # Spark's to_date yields null for unparseable input
        return None


# --- one function per mapped field: the unit MapVerity's risk model scores (see ownership.yaml) ---


def select_members(m: pd.DataFrame, d: Set[str]) -> pd.DataFrame:
    keep = {"A", "P"}
    if "D01_filter_includes_terminated" in d:
        keep = {"A", "P", "T"}
    elif "D02_filter_drops_pending" in d:
        keep = {"A"}
    return m[m["mbr_status_cd"].str.strip().isin(keep)]


def map_first_name(r, d: Set[str]) -> str:
    if "D04_name_case_not_normalised" in d:
        return r.mbr_first_name.strip()
    return r.mbr_first_name.strip().title()


def map_last_name(r, d: Set[str]) -> str:
    if "D03_name_not_trimmed" in d:
        return r.mbr_last_name.ljust(20).title()
    return r.mbr_last_name.strip().title()


def map_birth_date(r, d: Set[str]):
    fmt = "%Y%d%m" if "D05_dob_month_day_swapped" in d else "%Y%m%d"
    return _date(r.mbr_dob, fmt)


def map_gender(r, d: Set[str]):
    default = None if "D06_gender_unknown_mapped_null" in d else "UNKNOWN"
    return GENDER.get(r.mbr_gender_cd.strip(), default)


def map_monthly_income(r, d: Set[str]) -> float:
    scale = 100 if "D07_comp3_scale_lost" in d else 1
    return float(r.mbr_monthly_income) * scale


def map_household_size(r, d: Set[str]) -> int:
    if "D20_household_size_from_wrong_field" in d:
        return int(r.mbr_county_cd)
    return int(r.mbr_hh_size)


def compute_fpl_percent(income: float, hh: int, d: Set[str]) -> float:
    months = 144 if "D08_fpl_uses_annual_income_twice" in d else 12
    size_term = hh if "D09_fpl_hh_off_by_one" in d else hh - 1
    guideline = 15060 + 5380 * size_term
    return round(income * months / guideline * 100, 1)


def map_county(r, d: Set[str]) -> str:
    if "D10_county_leading_zero_lost" in d:
        return str(int(r.mbr_county_cd))
    return r.mbr_county_cd.strip().zfill(2)


def format_phone(r, d: Set[str]):
    p = r.mbr_phone.strip()
    if len(p) != 10:
        return None
    if "D11_phone_format_wrong" in d:
        return p
    return f"{p[:3]}-{p[3:6]}-{p[6:]}"


def map_status(r, d: Set[str]) -> str:
    table = {"A": "PENDING", "P": "ACTIVE"} if "D12_status_lookup_swapped" in d else STATUS
    return table.get(r.mbr_status_cd.strip(), "TERMINATED")


def map_end_date(s, d: Set[str]):
    end = _date(s.END_DT) if isinstance(s.END_DT, str) else None
    if end is None and "D15_open_end_date_defaulted" in d:
        return "9999-12-31"
    if end and "D14_segment_end_date_shifted" in d and end != "9999-12-31":
        end = (dt.date.fromisoformat(end) - dt.timedelta(days=1)).isoformat()
    return end


def map_aid_category(s, d: Set[str]) -> str:
    if "D17_aid_category_not_uppercased" in d:
        return s.AID_CAT.strip()
    return s.AID_CAT.strip().upper()


def map_plan_code(s, d: Set[str]):
    if "D16_plan_code_untranslated" in d:
        return s.PLAN_CD.strip()
    return PLAN.get(s.PLAN_CD.strip())


def build_eligibility(segments: List, d: Set[str]) -> List[Dict]:
    if "D13_segments_inner_join_drops_last" in d:
        segments = segments[:1]
    out = []
    for s in segments:
        out.append(
            {
                "seq": int(s.SEG_SEQ),
                "aidCategory": map_aid_category(s, d),
                "startDate": _date(s.START_DT),
                "endDate": map_end_date(s, d),
                "planCode": map_plan_code(s, d),
            }
        )
    return out


def lineage(d: Set[str], load_ts: str) -> Dict[str, str]:
    return {"system": "LEGACY " if "D19_source_system_wrong" in d else "LEGACY", "loadTimestamp": load_ts}


def write_documents(docs: List[Dict], d: Set[str]) -> List[Dict]:
    if "D18_duplicate_documents" in d:
        return docs + [dict(x, _id=x["_id"] + "-dup") for x in docs[: max(1, len(docs) // 50)]]
    return docs


def transform(
    member_master: pd.DataFrame,
    elig: pd.DataFrame,
    defects: Set[str] = frozenset(),
    load_ts: str = "2026-10-30T12:00:00Z",
) -> List[Dict]:
    d = set(defects)
    segs: Dict[str, List] = {}
    for r in elig.itertuples(index=False):
        segs.setdefault(r.MBR_ID.strip(), []).append(r)
    docs = []
    for r in select_members(member_master, d).itertuples(index=False):
        mid = r.mbr_id.strip()
        first, last = map_first_name(r, d), map_last_name(r, d)
        income, hh = map_monthly_income(r, d), map_household_size(r, d)
        docs.append(
            {
                "_id": mid,
                "memberId": mid,
                "name": {"first": first, "last": last, "full": f"{first} {last}"},
                "birthDate": map_birth_date(r, d),
                "gender": map_gender(r, d),
                "countyCode": map_county(r, d),
                "householdSize": hh,
                "monthlyIncome": round(income, 2),
                "fplPercent": compute_fpl_percent(income, hh, d),
                "status": map_status(r, d),
                "contact": {"phone": format_phone(r, d)},
                "lastUpdated": _date(r.mbr_last_updt),
                "source": lineage(d, load_ts),
                "eligibility": build_eligibility(segs.get(mid, []), d),
            }
        )
    return write_documents(docs, d)


def read_sources(landing: Path):
    master = read_table(
        str(landing / "MEMBER.MASTER.dat"),
        "cobol",
        {"copybook": str(HERE / "copybooks" / "MEMBER-REC.cpy"), "encoding": "cp037"},
    )
    elig = read_table(str(landing / "ELIG_SEGMENTS.txt"), "psv")
    return master, elig


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--landing", default=str(HERE / "landing"))
    p.add_argument("--out", default=str(HERE / "output" / "members.jsonl"))
    p.add_argument("--defect", action="append", default=[], choices=sorted(DEFECTS))
    a = p.parse_args()
    master, elig = read_sources(Path(a.landing))
    docs = transform(master, elig, set(a.defect))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for doc in docs:
            fh.write(json.dumps(doc) + "\n")
    print(f"wrote {len(docs)} member documents to {a.out}")
