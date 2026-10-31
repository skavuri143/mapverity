# ADR 0002: The oracle shares no code with the pipeline

**Status:** Accepted, 2026-10-30

## Context
If tests reuse the transformation's code or engine (the same Spark job, the same copybook reader),
a defect in that shared code passes its own tests.

## Decision
MapVerity reads sources and targets itself (its own COBOL decoder, fixed-width reader, document
flattener) and re-derives expected values with pandas expressions, independent of the job's engine.

## Consequences
A defect in the job cannot hide itself. MapVerity must maintain its own readers, and engine-level
differences (rounding at .x5) need explicit per-field tolerances in the mapping sheet.
