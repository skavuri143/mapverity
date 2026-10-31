# Risk and ERAD-QA prioritization

**ERAD-QA** (Explainable Risk-Adaptive Data Quality Assurance) orders validation checks so that
the ones most likely to fail run first, per unit of execution cost, and explains every position.
It changes *when* a check runs, never *whether* a blocking check counts (see
[Quality gates](quality-gates.md)).

## Signals

| Signal | Source | Cost to compute |
|---|---|---|
| `hist` | exponentially-decayed failure rate of the check (Beta prior) from `.mapverity/history.json` | none |
| `last` | the check failed on the previous run | none |
| `change` | the check covers a data unit that changed: `mapverity diff-mapping`, or changed code units mapped through `ownership.yaml` | none |
| `risk` | predicted defect-proneness of the code that produces the covered fields (`mapverity risk score`), discounted for unchanged units | one model call per function |
| `drift` | profile drift of the covered columns against a known-good baseline (null rate, distinctness, mean shift, value-shape distribution) | one sampled profile per table |

```text
priority(c) = ( w_hist·hist + w_last·last + w_change·change + w_risk·risk + w_drift·drift ) / cost(c)^α
```

Weights default to fixed values and can be **learned from your own history** (logistic regression
of past check failures on the signals, non-negative, normalised); the cost exponent α can be
**tuned on past runs** to maximise APFDc. `mapverity plan` prints each check's top contributing
signals.

## Defect-risk model

A gradient-boosted (XGBoost), random-forest or logistic model over 18 static code metrics
(McCabe, Halstead, LOC) trained on the 12 cleaned NASA MDP projects (17,147 modules), with
optional SMOTE inside training folds and SHAP explanations. MapVerity computes the same metrics
for Python functions, so the model can score your transformation code. Read the
[model card](model-card.md) first.

## What the evaluation found

Full method, scripts and raw results: `experiments/erad_qa/`. Summary:

**Prioritization** (300 simulated releases of the member pipeline; 64 evaluated releases with
detectable defects; real NASA module labels decide which code changes are defective; 95%
bootstrap CIs):

| Ordering | APFDc | Share of defects found within 10% of suite cost |
|---|---|---|
| ERAD-QA (learned weights) | **0.962** [0.956, 0.967] | **95.6%** |
| change-based | 0.918 [0.900, 0.935] | 56.0% |
| cheapest-first | 0.658 [0.587, 0.733] | 24.0% |
| risk-only | 0.652 [0.585, 0.716] | 18.0% |
| random (mean of 20) | 0.604 [0.579, 0.630] | 16.4% |
| history | 0.594 [0.534, 0.660] | 10.9% |
| oracle (upper bound) | 0.979 [0.976, 0.983] | 96.9% |

ERAD-QA beat every non-oracle baseline (Wilcoxon p ≤ 7×10⁻⁶; Vargha-Delaney A12 = 0.69 against
change-based ordering, 1.00 against random).

**Ablations, including negative findings:**

- **Drift is the most valuable signal.** Without it APFDc falls to 0.911.
- **The code-metric risk signal did not help here.** Removing it *raised* APFDc to 0.970
  (p = 3×10⁻⁶). A model trained on public NASA projects transfers weakly to a new codebase
  (cross-project AUC ≈ 0.72), and in this pipeline the other signals already explained the failures.
  Treat `risk` as advisory, and keep it in the ordering only if your own history shows it helps.
- **Cost-awareness did not matter** in this suite: the tuned exponent was α = 0, because check
  runtimes were similar and failures concentrated in a few checks.
- **Overhead:** profiling and planning took about 20% of this fast suite's runtime (median), so the
  drift probe pays off only when checks are expensive relative to a sampled profile.

**Defect prediction** (within-project 5-fold CV ×3; leave-one-project-out):

| Model | Within-project AUC | Cross-project AUC |
|---|---|---|
| logistic regression | 0.771 ± 0.041 | 0.726 ± 0.043 |
| random forest | 0.763 ± 0.059 | 0.712 ± 0.053 |
| XGBoost | 0.760 ± 0.051 | 0.723 ± 0.048 |
| XGBoost + SMOTE | 0.766 ± 0.051 | 0.718 ± 0.042 |

Differences between models were not significant (XGBoost vs logistic, p = 0.42; SMOTE vs none on
MCC, p = 0.34). SHAP explanations were moderately stable across folds (Kendall τ = 0.64, top-5
Jaccard = 0.59).

## Threats to validity

The release study is semi-synthetic. Defects were injected only into changed code units (which
favours change signals), and every injected defect is one the mapping suite can detect. Test
costs are runtimes on 1,000-member runs. The pipeline, defects and operators were designed by the
author. These results are evidence that the method can work, not proof that it generalizes;
studies on production pipelines are the next step.
