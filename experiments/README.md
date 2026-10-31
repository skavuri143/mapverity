# Experiments

Every result MapVerity reports is produced by a script here, from synthetic or public data.
Results are committed so they can be checked without re-running; re-running reproduces them.

| Directory | Question | Run |
|---|---|---|
| `mapping_validation/` | Do mapping-derived checks catch realistic transformation defects, without false alarms, fast enough for CI? | `python experiments/mapping_validation/run_experiments.py` (~15 min) |
| `erad_qa/` | How accurate and stable is the explainable defect-risk model? Does ERAD-QA find defects earlier per unit of cost than standard orderings? | `python experiments/erad_qa/e5_defect_prediction.py` (~6 min), `python experiments/erad_qa/e6_prioritization.py` (~4 min) |

Environment: Python 3.11+, `pip install -e ".[dev]"`. The NASA MDP datasets are downloaded and
checksum-verified on first use (set `MAPVERITY_CACHE` to choose the cache directory).

Reporting rules: confidence intervals with every mean, the exact statistical test with every
comparison, ablations alongside the full method, and negative findings kept in the summary.
