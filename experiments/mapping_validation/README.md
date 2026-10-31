# Mapping-validation experiments

`run_experiments.py` (member pipeline, ~12 min) and `claims_study.py` (insurance claims, ~1 min).

## Member pipeline (2,000 members per run)

Suites: S1 structural (types, required, keys; 25 checks), S2 + domains (28), S3 profiled from one
known-good run (57), **S4 compiled from the mapping sheet (49)**, S5 = S4 ∪ S3 (106).

| Suite | Seeded Glue defects caught (of 20, 5 runs each) | Mutation score, 1% / 5% faulty rows | Clean runs with a false alarm (of 20) |
|---|---|---|---|
| S1 structural | 3 | 30.9% / 32.8% | 0 |
| S2 constraints | 5 | 41.8% / 42.9% | 0 |
| S3 profiled | 15 | 79.5% / 80.4% | 14 |
| **S4 mapping** | **20** | **99.0% / 99.0%** | **0** |
| S5 mapping + profiled | 20 | 100% / 100% | 14 |

Mutation scores are means of 3 seeds (standard deviation ≤ 1.7 points), 192 mutants per run.
S4's only surviving mutants alter the load timestamp, the one field the mapping marks as not
derivable from source. S3's false alarms come from learned value ranges (income, poverty-level
percentage) that new, correct data exceeds.

Runtime of S4 (median of 3, 2 vCPUs): 1,000 members 0.32 s; 20,000 members 1.7 s; 100,000 members
(163,107 eligibility segments) 8.7 s, plus 1.8 s COBOL decoding and 1.6 s document flattening.

## Insurance claims (800 claims, 2,841 service lines)

The 54 checks compiled from `examples/insurance_claims/mapping/claims_mapping.csv` pass on the
clean run, catch **10 of 10** seeded defects, and kill **113/113** mutants on claim documents and
**67/67** on service lines (5% faulty rows).
