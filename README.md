<div align="center">

# MapVerity

**Field-level assurance for data pipelines.**

Prove that every field was transformed correctly from source to target, measure how strong your
tests really are, and run the riskiest checks first.

[![CI](https://github.com/YOUR-GITHUB-USER/mapverity/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-GITHUB-USER/mapverity/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mapverity.svg)](https://pypi.org/project/mapverity/)
[![Python](https://img.shields.io/pypi/pyversions/mapverity.svg)](https://pypi.org/project/mapverity/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

</div>

---

Healthcare, insurance, government and financial data platforms move records through long chains
of files, transformation jobs and databases: mainframe extracts and partner flat files land in
S3, a Glue/Spark/ETL job reshapes them according to a data model, and the result lands in a
document store, warehouse or API. Most defects in those chains produce output that still *looks*
fine: well-typed, non-null, in-domain, and wrong.

MapVerity turns the **source-to-target mapping sheet** your analysts already maintain into an
executable test suite. For every mapped field, it re-derives the expected value from the source
record and compares it with what the pipeline actually wrote.

```
 sources                         your pipeline                     target
 COBOL / EBCDIC (copybook) ─┐
 fixed-width (layout)        ├──► S3 ──► Glue / Spark / ETL ──► DocumentDB / MongoDB
 CSV / PSV / JSON / Parquet  │                                     SQL warehouse
 SQL tables, REST APIs ──────┘                                     REST API
                │                                                      │
                └──────────────► MapVerity (independent oracle) ◄──────┘
                                 mapping sheet ─► executable checks
```

## What it does

| | |
|---|---|
| **Mapping-driven validation** | Compiles a CSV/Excel mapping sheet into checks: field values (direct, trim, case, code lookups, date formats, concatenation, constants, expressions), record completeness under the job's selection filter, nested arrays matched by composite key, array cardinality, foreign keys, patterns, required fields and domains. |
| **Readers for real-world sources** | COBOL copybooks (EBCDIC, COMP-3, binary, zoned decimal, OCCURS, REDEFINES), multi-record fixed-width layouts with control totals, delimited/JSON/Parquet, S3 objects and prefixes, DocumentDB/MongoDB, any SQLAlchemy database, paginated REST APIs. |
| **Coverage you can report** | Which data-model fields are value-validated, constraint-only, not validated, unmapped or missing. |
| **Mutation testing for data** | Injects 15 classes of realistic ETL faults into the output and reports the share your suite catches, plus the exact blind spots. |
| **Explainable risk (AI)** | A defect-risk model trained on public defect data scores your transformation functions from their code metrics, with SHAP explanations. Inferred checks are mined from known-good data; optional LLM rule proposals are executed and kept only if the data supports them. |
| **ERAD-QA prioritization (AI)** | Runs the checks most likely to fail first, per unit of cost, from history, code change, predicted risk and observed data drift, and explains every position. |
| **Deterministic quality gates** | PASS / WARN / BLOCK / INCOMPLETE from evidence. Proven integrity failures always block; predicted risk is advisory unless your policy says otherwise. |
| **Fits your CI** | CLI, pytest plugin, Python API; JUnit, HTML, JSON, Markdown and Allure output; GitHub Actions and AWS CodePipeline templates; Docker image. |

## Quickstart (15 minutes, no cloud account)

```bash
pip install "mapverity[all]"
git clone https://github.com/YOUR-GITHUB-USER/mapverity && cd mapverity/examples/member_eligibility

python generate_data.py          # synthetic EBCDIC member master + eligibility segments
python transform.py              # the pipeline under test: writes member JSON documents

mapverity from-mapping mapping/member_data_model_mapping.csv --datasets datasets.yaml -o member.mapverity.yaml
mapverity validate member.mapverity.yaml --html report.html
#   49 passed · 0 failed · 0 errors

python transform.py --defect D07_comp3_scale_lost     # break the pipeline on purpose
mapverity validate member.mapverity.yaml
#   | map:members:monthlyIncome | failed | 1668 | 1668 row(s) where 'monthlyIncome' != col("mbr_monthly_income") |
```

Then measure the suite and gate a release:

```bash
python transform.py
mapverity mutate member.mapverity.yaml --dataset members --json mutation.json   # 98.6% of injected faults caught
mapverity validate member.mapverity.yaml --mapping mapping/member_data_model_mapping.csv \
    --mutation-report mutation.json --gates gates.yaml --html report.html --junit results.xml
#   Quality gate: PASS
```

A second example, [`examples/insurance_claims`](examples/insurance_claims), validates a
multi-record fixed-width claims file (header, claims, service lines, trailer control totals)
against claim documents with nested lines.

## The mapping sheet

One row per target field; column names are flexible (`transformation` = `rule`, and so on).

| target_table | target_field | source_dataset | source_field | rule | data_type | required | key | allowed_values | pattern | references |
|---|---|---|---|---|---|---|---|---|---|---|
| members | memberId | member_master | mbr_id | trim | string | Y | Y | | | |
| members | gender | member_master | mbr_gender_cd | `lookup: M=MALE, F=FEMALE, *=UNKNOWN` | string | Y | | MALE\|FEMALE\|UNKNOWN | | |
| members | birthDate | member_master | mbr_dob | `date: %Y%m%d` | date | Y | | | | |
| claims | policyNumber | claims_file.CLM | policy_no | trim | string | Y | | | | policies.policy_no |
| claims.lines | lineNo | claims_file.LIN | line_no | direct | integer | Y | Y | | | |

Rules: `direct`, `trim`, `upper`, `lower`, `title`, `upper_trim`, `lookup:`, `date:`, `concat:`,
`constant:`, `count`, `ref:`, `none`, and `expr:` for anything else. See the
[mapping sheet reference](docs/mapping-sheet.md).

## AI-assisted prioritization and risk

```bash
mapverity risk train --out defect-risk.pkl                     # public NASA MDP data, checksum-verified
mapverity risk score transform.py --model defect-risk.pkl --ownership ownership.yaml -o risk.json
#   | transform:compute_fpl_percent | 0.41 | CYCLOMATIC_COMPLEXITY +0.32, ... |
mapverity diff-mapping old_mapping.csv new_mapping.csv          # which fields changed
mapverity plan member.mapverity.yaml --history .mapverity/history.json --risk risk.json --changed members.gender
mapverity validate member.mapverity.yaml --prioritize --history .mapverity/history.json --fail-fast
```

Risk scores and ordering never replace a check: blocking checks always run, and a run cut short
by a time budget is `INCOMPLETE`, not `PASS`. Read the [model card](docs/model-card.md) before
relying on risk scores; its limitations section matters.

## Evidence

Every number below is reproduced by scripts in [`experiments/`](experiments/), with confidence
intervals, ablations and negative findings. All data is synthetic or public.

| Study | Result |
|---|---|
| 20 realistic Glue-job defects (member pipeline) | mapping-derived checks caught **20/20**; data-model constraints caught 5/20; profiled inference 15/20 |
| 10 realistic defects (insurance claims pipeline) | mapping-derived checks caught **10/10** |
| Data mutation testing | **99.0%** of mutants killed (member), **100%** (claims) |
| False alarms on 20 independent clean runs | **0** for mapping-derived checks |
| Runtime | 100,000 member documents (163,107 nested segments) validated with 49 checks in 8.7 s on 2 vCPUs |
| ERAD-QA prioritization (300 simulated releases) | APFDc **0.962** vs 0.918 for change-based ordering and 0.604 random; 95.6% of defects found within the first 10% of test cost |
| Defect-risk model (12 NASA MDP projects) | cross-project AUC about 0.72; no significant gain from XGBoost over logistic regression or from SMOTE; **the risk signal did not improve prioritization** in our study, so it stays advisory |

## Install options

```bash
pip install mapverity                 # core: pandas, numpy, pyyaml, typer
pip install "mapverity[aws]"          # S3 (boto3) and DocumentDB/MongoDB (pymongo)
pip install "mapverity[sql]"          # any SQLAlchemy database (add your driver)
pip install "mapverity[api]"          # REST sources (httpx)
pip install "mapverity[ai]"           # risk model and prioritization (scikit-learn, xgboost, shap, imbalanced-learn)
pip install "mapverity[all]"
docker run --rm -v "$PWD:/work" ghcr.io/YOUR-GITHUB-USER/mapverity validate spec.mapverity.yaml
```

## Documentation

[Getting started](docs/getting-started.md) ·
[Mapping sheet](docs/mapping-sheet.md) ·
[Sources and targets](docs/sources-and-targets.md) ·
[Checks reference](docs/checks.md) ·
[Mutation testing](docs/mutation-testing.md) ·
[Risk and ERAD-QA](docs/erad-qa.md) ·
[Quality gates](docs/quality-gates.md) ·
[CI/CD](docs/ci-cd.md) ·
[Model card](docs/model-card.md) ·
[Architecture decisions](docs/adr/)

## Project

- **License:** Apache-2.0. **Status:** alpha; the spec format may change before 1.0 (see [CHANGELOG](CHANGELOG.md)).
- **Contributing:** see [CONTRIBUTING](CONTRIBUTING.md); please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Security:** report vulnerabilities privately as described in [SECURITY](SECURITY.md).
- **Support:** see [SUPPORT](SUPPORT.md). **Governance:** see [GOVERNANCE](GOVERNANCE.md).
- **Using MapVerity?** We would like to hear how; see [ADOPTERS](ADOPTERS.md).
- **Citing:** see [CITATION.cff](CITATION.cff).

MapVerity never needs real personal, member, patient or policyholder data to be tested or
demonstrated: every example and test runs on generated data. Do not attach real records to
issues.
