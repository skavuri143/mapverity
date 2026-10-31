# ERAD-QA experiments

## E5: explainable defect-risk prediction (`e5_defect_prediction.py`)

12 cleaned NASA MDP projects (17,147 modules), 18 portable static metrics. Within-project:
repeated stratified 5-fold CV (3 repeats). Cross-project: leave-one-project-out. SMOTE is fitted
inside training folds only. Means over projects with 95% t-intervals.

| Model | Within AUC | Within MCC | Cross AUC | Cross MCC |
|---|---|---|---|---|
| logistic regression | 0.771 ± 0.041 | 0.289 ± 0.056 | 0.726 ± 0.043 | 0.208 ± 0.034 |
| random forest | 0.763 ± 0.059 | 0.235 ± 0.087 | 0.712 ± 0.053 | 0.121 ± 0.040 |
| XGBoost | 0.760 ± 0.051 | 0.267 ± 0.084 | 0.723 ± 0.048 | 0.200 ± 0.045 |
| XGBoost + SMOTE | 0.766 ± 0.051 | 0.283 ± 0.072 | 0.718 ± 0.042 | 0.205 ± 0.045 |

- XGBoost vs logistic regression (within-project AUC, Wilcoxon over projects): p = 0.42, no significant difference.
- SMOTE vs none (XGBoost, within-project MCC): p = 0.34, no significant difference.
- Explanation stability (SHAP global importance across 15 folds per project): Kendall τ = 0.64 ± 0.05, top-5 Jaccard = 0.59 ± 0.04.

Per-project and per-fold numbers: `results/e5_summary.json`, `results/e5_within_project_folds.csv`, `results/e5_cross_project.csv`.

## E6: risk-adaptive prioritization (`e6_prioritization.py`)

300 simulated releases of the member pipeline (1,000 members each, fresh data per release), 18
code units, 20 realistic defects, 49 mapping-derived checks. In each release 1–5 code units
change; each change takes a real NASA module whose real label decides whether a defect is
injected, and whose metrics feed a leave-one-project-out risk model. Releases 1–100 are warm-up
(weights and cost exponent learned); 101–300 are evaluated. 64 evaluated releases contained
detectable defects. 95% bootstrap CIs over releases; Wilcoxon signed-rank and Vargha-Delaney A12
against ERAD-QA (learned).

| Ordering | APFDc | Cost to first failure (share of suite) | Defects found within 10% of cost | vs ERAD: p, A12(ERAD >) |
|---|---|---|---|---|
| **ERAD-QA (learned)** | **0.962** [0.956, 0.967] | **0.052** | **95.6%** | |
| ERAD-QA (default weights) | 0.939 [0.929, 0.950] | 0.072 | 69.8% | 7×10⁻⁶, 0.73 |
| change-based | 0.918 [0.900, 0.935] | 0.091 | 56.0% | 4×10⁻⁶, 0.69 |
| cheapest-first | 0.658 [0.587, 0.733] | 0.338 | 24.0% | 2×10⁻¹⁰, 0.80 |
| risk-only | 0.652 [0.585, 0.716] | 0.345 | 18.0% | 7×10⁻¹², 0.91 |
| random (mean of 20) | 0.604 [0.579, 0.630] | 0.391 | 16.4% | 4×10⁻¹², 1.00 |
| history | 0.594 [0.534, 0.660] | 0.405 | 10.9% | 9×10⁻¹², 0.91 |
| oracle | 0.979 [0.976, 0.983] | 0.034 | 96.9% | |

Ablations of ERAD-QA (learned): without drift 0.911 (largest loss); without history 0.959;
without last-failure, change or cost 0.962 (no effect; tuned α was 0). **Without risk 0.970:
removing the code-metric risk signal improved ordering** (p = 3×10⁻⁶). Learned weights: drift
1.93, risk 1.24, history 1.02, change 0.68, last 0.13. Planning overhead: median 19% of suite
runtime.

### Threats to validity
Defects occur only in changed units (favours change signals); every injected defect is detectable
by the suite; one pipeline; author-designed defects; costs measured on small runs. See
`docs/erad-qa.md`.
