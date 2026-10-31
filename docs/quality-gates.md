# Quality gates

`mapverity validate ... --gates gates.yaml` turns evidence into a release decision.

| Outcome | Exit code | Meaning |
|---|---|---|
| `PASS` | 0 | all evidence meets policy |
| `WARN` | 0 | advisory findings (warning-level checks, predicted risk) |
| `INCOMPLETE` | 1 | required evidence missing, or blocking checks not run (time budget) |
| `BLOCK` | 1 | a blocking check failed, or a threshold the policy enforces was missed |

Rules that never change:

- A failed check with `severity: error` always **blocks**. No score outweighs a proven failure.
- Checks cut by `--budget` or `--fail-fast` make the decision `INCOMPLETE`, never `PASS`.
- Predicted risk produces `WARN` unless the policy explicitly sets `risk_blocks: true`.

```yaml
gates:
  min_value_coverage: 0.9       # share of mapped fields whose values are re-derived
  coverage_action: BLOCK        # or WARN
  min_mutation_score: 0.9       # needs --mutation-report
  mutation_action: WARN
  max_warn_failures: 5          # more warning-level failures than this → BLOCK
  risk_warn_threshold: 0.7      # needs --risk
  risk_blocks: false
  require_all_blocking_checks: true
```

Unknown keys are rejected, so a typo cannot silently disable a gate.
