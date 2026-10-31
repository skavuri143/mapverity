"""Generate a SYNTHETIC claims file in a multi-record fixed-width layout, plus a policy extract.

    landing/CLAIMS.<date>.txt   HDR / CLM / LIN ... / TRL records (layouts/CLAIMS-FILE.layout.csv)
    reference/policies.csv      policy extract the claims must reference

All identifiers, providers and amounts are generated. Procedure and diagnosis codes are drawn
from a short list of common, publicly documented codes for realism only.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
PROCS = ["99213", "99214", "99203", "36415", "80053", "85025", "71046", "93000", "97110", "D0120", "D1110", "J1885"]
MODS = ["", "", "", "25", "59", "GT", "RT", "LT"]
POS = ["11", "11", "22", "23", "02", "21", "81"]
DX = ["E119", "I10", "J069", "M5450", "Z0000", "R109", "K219", "F419", "E785", "N390"]
STATUS = ["01", "01", "01", "01", "02", "04", "22"]


def amount(v: float, width: int) -> str:
    cents = int(round(abs(v) * 100))
    return str(cents).zfill(width - 1) + ("-" if v < 0 else " ")


def generate(n: int, seed: int, out: Path, ref: Path):
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    ref.mkdir(parents=True, exist_ok=True)
    policies = [f"PL{seed % 100:02d}{i:08d}" for i in range(1, max(50, n // 4) + 1)]
    with open(ref / "policies.csv", "w", encoding="utf-8") as fh:
        fh.write("policy_no,plan_code,policy_status\n")
        for p in policies:
            fh.write(
                f"{p},{rng.choice(['PPO', 'HMO', 'EPO', 'DENTAL'])},{rng.choice(['ACTIVE', 'ACTIVE', 'LAPSED'])}\n"
            )
    file_date = dt.date(2026, 10, 30)
    lines = [f"HDR{file_date:%Y%m%d}{'PARTNER001':<10}"]
    n_lines = 0
    total_paid_file = 0.0
    for i in range(1, n + 1):
        status = str(rng.choice(STATUS))
        ctype = str(rng.choice(["P", "P", "P", "I", "D"]))
        start = file_date - dt.timedelta(days=int(rng.integers(5, 200)))
        end = start + dt.timedelta(days=int(rng.integers(0, 4)))
        k = int(rng.integers(1, 7))
        detail = []
        for ln in range(1, k + 1):
            units = int(rng.integers(1, 5))
            billed = round(float(rng.gamma(2.0, 80.0)) * units, 2)
            paid = (
                0.0
                if status in ("02", "04") or rng.random() < 0.12
                else round(billed * float(rng.uniform(0.4, 0.85)), 2)
            )
            if status == "22":
                billed, paid = -billed, -paid
            detail.append(
                (ln, str(rng.choice(PROCS)), str(rng.choice(MODS)), str(rng.choice(POS)), units, billed, paid)
            )
        tb = round(sum(d[5] for d in detail), 2)
        tp = round(sum(d[6] for d in detail), 2)
        ta = round(tp + (0 if tp == 0 else abs(tp) * float(rng.uniform(0, 0.25)) * (1 if tp > 0 else -1)), 2)
        cid = f"C{seed % 100:02d}{i:012d}"
        lines.append(
            "CLM" + f"{cid:<15}{rng.choice(policies):<12}{ctype}{status}{start:%Y%m%d}{end:%Y%m%d}"
            f"{int(rng.integers(1000000000, 1999999999)):010d}{rng.choice(DX):<7}"
            + amount(tb, 12)
            + amount(ta, 12)
            + amount(tp, 12)
        )
        for ln, proc, mod, pos, units, billed, paid in detail:
            lines.append(
                "LIN" + f"{cid:<15}{ln:03d}{proc:<5}{mod:<2}{pos:<2}{units:04d}" + amount(billed, 10) + amount(paid, 10)
            )
            n_lines += 1
        total_paid_file += tp
    lines.append("TRL" + f"{n:09d}" + amount(round(total_paid_file, 2), 15))
    path = out / f"CLAIMS.{file_date:%Y%m%d}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n, n_lines, path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--claims", type=int, default=800)
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()
    c, ln, path = generate(a.claims, a.seed, HERE / "landing", HERE / "reference")
    print(f"wrote {c} claims with {ln} service lines to {path}")
