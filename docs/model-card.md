# Model card: MapVerity defect-risk model

## Intended use
Advisory ranking of transformation functions by likelihood of containing a defect, to prioritize
review and validation. **Not** for evaluating people, not a substitute for running checks, and
never a release blocker unless a policy explicitly opts in.

## Model
XGBoost (default) or random forest / logistic regression over 18 static metrics: LOC_TOTAL,
LOC_EXECUTABLE, LOC_COMMENTS, LOC_CODE_AND_COMMENT, CYCLOMATIC_COMPLEXITY, BRANCH_COUNT and 12
Halstead measures. Features are log1p-scaled; SMOTE is optional and applied inside training folds
only. Explanations: SHAP (TreeExplainer/LinearExplainer); optional LIME.

## Training data
The 12 cleaned NASA Metrics Data Program projects (Shepperd et al. D'' versions; 17,147 modules;
1.8%–35.5% defective). Downloaded and checksum-verified at run time, not redistributed; see
[data provenance](data/nasa-mdp.md).

## Evaluation (experiments/erad_qa/e5_defect_prediction.py)
- Within-project AUC 0.76–0.77 averaged over projects (range 0.62–0.92 by project).
- Cross-project (leave-one-project-out) AUC about 0.72; this is the realistic setting for a new codebase.
- No significant difference between model families; SMOTE gave no significant MCC gain.
- SHAP global rankings: Kendall τ 0.64, top-5 Jaccard 0.59 across folds.

## Limitations
- **Domain shift:** NASA modules are C/C++/Java flight and ground software from the 1990s–2000s;
  your code is likely Python/Spark. MapVerity's Python metrics approximate the original tool's
  definitions (documented in `ai/codemetrics.py`). Expect lower accuracy than the cross-project numbers.
- In MapVerity's own prioritization study the risk signal **did not improve** ordering (see
  [ERAD-QA](erad-qa.md)). Validate it on your history before relying on it.
- Size metrics dominate explanations: larger, more complex functions score higher. That is a
  correlation in the training data, not a causal claim about any function.
- Labels in public defect datasets are noisy (Shepperd et al. 2013).

## Ethical considerations
Metrics describe code, not authors. Do not use scores to evaluate individual developers.
