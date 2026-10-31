# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| latest 0.x minor release | yes |
| older 0.x releases | no; please upgrade |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

- Preferred: GitHub private vulnerability reporting ("Report a vulnerability" on the repository's
  Security tab).
- Or email srikanthkavuri.research@gmail.com with the subject "MapVerity security".

Include the affected version, a description, and steps to reproduce using synthetic data only.
You will receive an acknowledgement within 3 business days and a status update within 10 business
days. Fixes are released as patch versions and credited in the changelog unless you prefer
otherwise.

## Security model (read before deploying)

- **Spec files, mapping sheets and gate policies are trusted configuration**, like a CI file.
  Expressions (`expr:` rules, `row_rule` checks) are evaluated with Python builtins disabled, but
  that is not a sandbox. Do not load specs from untrusted sources.
- **Model files are pickles.** `DefectRiskModel.load` refuses files whose SHA-256 does not match
  the recorded checksum, but you should still only load models you trained or obtained from a
  source you trust.
- **Credentials** belong in environment variables or a secrets manager. `${VAR}` references in
  dataset paths are expanded at run time; never commit credentials in specs.
- **Data in reports:** failure samples (up to 5 values per check) are included in JSON, HTML,
  JUnit and Allure output. When validating sensitive data, treat reports as sensitive, restrict
  access to CI artifacts, or run with synthetic fixtures (see `ci/aws/`).
- **Network access** happens only when you configure it: S3, databases, REST sources, the NASA
  MDP download (`mapverity datasets`/`risk train`, checksum-verified), and the optional LLM
  endpoint you configure.
