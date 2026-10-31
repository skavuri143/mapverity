# health-benefits member eligibility pipeline

```text
landing/MEMBER.MASTER.dat (EBCDIC, copybooks/MEMBER-REC.cpy) ─┐
landing/ELIG_SEGMENTS.txt (pipe-delimited)                     ├─► transform.py / glue_job.py ─► output/members.jsonl (DocumentDB)
```

```bash
python generate_data.py && python transform.py
mapverity from-mapping mapping/member_data_model_mapping.csv --datasets datasets.yaml -o member.mapverity.yaml
mapverity validate member.mapverity.yaml --html report.html
python transform.py --help                       # 20 seeded defects (D01..D20)
mapverity risk train --out model.pkl && mapverity risk score transform.py --model model.pkl --ownership ownership.yaml
```

| File | Purpose |
|---|---|
| `mapping/member_data_model_mapping.csv` | the data-model mapping (23 fields) |
| `copybooks/MEMBER-REC.cpy` | COBOL layout of the member master |
| `transform.py` | pandas reference implementation of the job, one function per field, with seeded defects |
| `glue_job.py` | the same transformation as an AWS Glue (PySpark + Cobrix) job |
| `ownership.yaml` | which function produces which fields (for risk and change impact) |
| `gates.yaml` | release policy |
| `datasets.yaml` / `datasets.aws.yaml` | local files / S3 + DocumentDB |

The federal poverty level guideline values are the published 2024 HHS figures for the 48
contiguous states, used only to make the example realistic.
