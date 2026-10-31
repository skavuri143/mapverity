"""Generate SYNTHETIC landing-zone files for the member pipeline example.

    landing/MEMBER.MASTER.dat     EBCDIC fixed-length records described by copybooks/MEMBER-REC.cpy
    landing/ELIG_SEGMENTS.txt     pipe-delimited eligibility segments (YYYYMMDD dates)

No real person or member data is used; names are drawn from small generic lists.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np

from mapverity.validation.cobol import Layout, write_cobol

HERE = Path(__file__).parent
FIRST = [
    "james",
    "mary",
    "robert",
    "patricia",
    "john",
    "jennifer",
    "michael",
    "linda",
    "david",
    "elizabeth",
    "maria",
    "jose",
    "aisha",
    "wei",
    "priya",
    "omar",
    "chloe",
    "liam",
    "noah",
    "emma",
    "ava",
    "lucas",
]
LAST = [
    "smith",
    "johnson",
    "williams",
    "brown",
    "jones",
    "garcia",
    "miller",
    "davis",
    "rodriguez",
    "martinez",
    "lee",
    "walker",
    "hall",
    "young",
    "king",
    "wright",
    "lopez",
    "hill",
    "scott",
    "green",
    "adams",
    "baker",
]
AID = ["MA", "CHIP", "ABD", "PW", "FP", "QMB"]
PLANS = ["FFS", "MCO1", "MCO2", "MCO3", "MCO4", "MCO5"]


def generate(n: int, seed: int, out_dir: Path):
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = Layout.from_file(HERE / "copybooks" / "MEMBER-REC.cpy")
    members, segments = [], []
    base = dt.date(2024, 1, 1)
    for i in range(n):
        mid = f"M{seed % 100:02d}{i + 1:07d}"
        dob = dt.date(1940, 1, 1) + dt.timedelta(days=int(rng.integers(0, 365 * 84)))
        first = str(rng.choice(FIRST))
        first = first.upper() if rng.random() < 0.5 else first  # mixed casing, as mainframes deliver it
        hh = int(rng.integers(1, 9))
        income = round(float(rng.gamma(2.0, 900.0)), 2)
        phone = "" if rng.random() < 0.06 else f"555{int(rng.integers(2000000, 9999999)):07d}"
        status = str(rng.choice(["A", "A", "A", "A", "P", "T"]))
        members.append(
            {
                "mbr_id": mid,
                "mbr_first_name": first,
                "mbr_last_name": str(rng.choice(LAST)).upper(),
                "mbr_dob": int(dob.strftime("%Y%m%d")),
                "mbr_gender_cd": str(rng.choice(["M", "F", "F", "M", "U"])),
                "mbr_county_cd": f"{int(rng.integers(1, 100)):02d}",
                "mbr_hh_size": hh,
                "mbr_monthly_income": income,
                "mbr_status_cd": status,
                "mbr_phone": phone,
                "mbr_last_updt": int((base + dt.timedelta(days=int(rng.integers(0, 600)))).strftime("%Y%m%d")),
            }
        )
        start = base - dt.timedelta(days=int(rng.integers(0, 900)))
        for seq in range(1, int(rng.integers(1, 4)) + 1):
            length = int(rng.integers(90, 400))
            end = start + dt.timedelta(days=length)
            open_ended = rng.random() < 0.3 and seq > 0
            segments.append(
                {
                    "MBR_ID": mid,
                    "SEG_SEQ": seq,
                    "AID_CAT": str(rng.choice(AID)).lower() if rng.random() < 0.2 else str(rng.choice(AID)),
                    "START_DT": start.strftime("%Y%m%d"),
                    "END_DT": "" if open_ended else end.strftime("%Y%m%d"),
                    "PLAN_CD": str(rng.choice(PLANS)),
                }
            )
            if open_ended:
                break
            start = end + dt.timedelta(days=1)
    write_cobol(members, layout, out_dir / "MEMBER.MASTER.dat", encoding="cp037")
    with open(out_dir / "ELIG_SEGMENTS.txt", "w", encoding="utf-8") as fh:
        fh.write("MBR_ID|SEG_SEQ|AID_CAT|START_DT|END_DT|PLAN_CD\n")
        for s in segments:
            fh.write(
                "|".join(str(s[k]) for k in ("MBR_ID", "SEG_SEQ", "AID_CAT", "START_DT", "END_DT", "PLAN_CD")) + "\n"
            )
    return len(members), len(segments)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--members", type=int, default=2000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default=str(HERE / "landing"))
    a = p.parse_args()
    m, s = generate(a.members, a.seed, Path(a.out))
    print(f"wrote {m} member records and {s} eligibility segments to {a.out}")
