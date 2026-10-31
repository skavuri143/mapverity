# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-31

### Added
- Mapping-sheet compiler: field derivation rules, keys and composite keys, selection filters, nested
  arrays with parent-aware completeness, array cardinality, references, patterns, tolerances;
  mapping coverage report
- 16 check types, including source-to-target `mapping`, `key_match`, `group_aggregate` and file
  `control_total`
- Readers: COBOL copybooks, multi-record fixed-width layouts, CSV/PSV/TSV/JSON/JSONL/Parquet,
  S3 objects and prefixes, DocumentDB/MongoDB, SQLAlchemy databases, paginated REST APIs; document
  flattener for nested JSON
- OpenAPI/JSON Schema contract compiler for API responses
- Data mutation testing with 15 fault operators
- Spec inference from known-good data with source-to-target relation mining
- Explainable defect-risk model (XGBoost/scikit-learn, SMOTE, SHAP, optional LIME) trained on the
  public NASA MDP datasets, Python code-metrics extractor, explanation-stability measures
- ERAD-QA risk-adaptive prioritization with learned weights, cost-exponent tuning, ablations,
  APFD/APFDc metrics, time-budgeted and fail-fast execution
- Deterministic quality gates (PASS/WARN/BLOCK/INCOMPLETE)
- CLI (Typer), pytest plugin, JUnit/JSON/Markdown/HTML/Allure reports
- Examples: health-benefits member eligibility pipeline (EBCDIC to DocumentDB), insurance claims (fixed-width to documents)
- GitHub Actions, AWS CodePipeline/CodeBuild templates, Docker image and compose demo
- Reproducible experiments for mapping validation and ERAD-QA
