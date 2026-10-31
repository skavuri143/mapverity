# Contributing to MapVerity

Thank you for helping. Bug reports from real pipelines (reduced to synthetic data), new mapping
rules, readers, fault operators and documentation fixes are all valuable.

## Ground rules

- **No real data, ever.** Do not commit or attach personal, member, patient, policyholder, claim or
  financial records, even "anonymized". Reproduce problems with generated data; see the generators
  in `examples/`.
- **No employer or client code.** Contribute only work you have the right to license under
  Apache-2.0.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

```bash
git clone https://github.com/YOUR-GITHUB-USER/mapverity && cd mapverity
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install            # optional: runs ruff on commit
```

## Before you open a pull request

```bash
ruff check src tests examples && ruff format --check src tests examples
mypy src
python -m coverage run -m pytest && python -m coverage report
```

- Add tests for every bug fix and every new check, rule, reader, operator or gate.
- Keep the core dependency-light (pandas, numpy, pyyaml, typer); integrations go behind extras.
- Changes to the spec format, public API, gate semantics or evaluation methodology need an ADR
  (`docs/adr/`), see [GOVERNANCE](GOVERNANCE.md).
- Update `CHANGELOG.md` under "Unreleased".
- Results reported in docs must be reproducible by a script in `experiments/`; report negative
  findings too.

## Developer Certificate of Origin

Sign off every commit (`git commit -s`) to certify the
[Developer Certificate of Origin](https://developercertificate.org/): you wrote the change or
otherwise have the right to submit it under the project license.

## Good first contributions

Look for issues labelled `good first issue`. Common areas: COBOL `OCCURS DEPENDING ON`, new
mapping rules, Avro/XML readers, DynamoDB/BigQuery targets, fault operators you have seen in
production, and documentation examples for your domain.
