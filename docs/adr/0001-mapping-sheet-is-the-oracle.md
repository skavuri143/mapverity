# ADR 0001: The mapping sheet is the test oracle

**Status:** Accepted, 2026-10-30

## Context
Data teams already specify transformations in source-to-target mapping sheets that analysts,
engineers and testers all read. Tests written separately drift from that specification, and
generic data-quality rules (types, nulls, domains) cannot tell whether a value was *derived*
correctly.

## Decision
Compile the mapping sheet into executable checks: one value-derivation check per mapped field,
plus completeness, cardinality, reference, pattern and domain checks. Keep the rule vocabulary
close to how analysts write mappings (`trim`, `lookup:`, `date:`), with `expr:` as an escape hatch.

## Consequences
A rule changed in the sheet changes the tests on the next build. Coverage can be reported in
terms of the data model. Fields whose values cannot be derived from source (run-time timestamps)
are visible as constraint-only rather than silently untested.
