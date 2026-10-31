# ADR 0003: Predicted risk is advisory; integrity failures always block

**Status:** Accepted, 2026-10-30

## Context
Probabilistic risk scores are useful for ordering work but can be wrong, and MapVerity's own
evaluation found that a model trained on public data transfers weakly to new codebases.

## Decision
Quality gates are deterministic: any failed blocking check blocks, unrun blocking checks make the
result INCOMPLETE, and risk only produces WARN unless a policy explicitly sets `risk_blocks: true`.
Prioritization changes the order of checks, never whether a blocking check counts.

## Consequences
Releases are never blocked or passed on a model's guess alone. Teams that want risk-based
blocking must opt in, visibly, in a reviewed policy file.
