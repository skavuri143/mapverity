# Checks reference

Generated from the code by `docs/gen_checks.py`; a test fails if a registered check is missing here.

Every check accepts `type`, `name` (optional id), `dataset`, `column` (when relevant) and
`severity: error | warn`. Other keys are the check's parameters.

## `aggregate_match`

Reconcile an aggregate between source and target (e.g. total paid amount).

params: source, target, source_expression, target_expression (or column), agg, tolerance_pct,
        source_filter

## `allowed_values`

Non-null values are in a fixed set. params: values: [...]; nulls are ignored (pair with not_null).

## `control_total`

File control totals: a trailer/header value equals an aggregate over the detail records.

params: control_dataset, control_column (the trailer value), dataset + expression/column (details),
        agg: count|sum|min|max (default count), tolerance

## `group_aggregate`

Aggregation pipelines: target.column == agg(source expression) grouped by key.

params: source, target, key | source_key/target_key, expression, agg, tolerance, source_filter

## `in_range`

Values lie within bounds. params: min, max (either optional), kind: number|date. Unparseable values fail.

## `key_match`

Target keys vs (filtered) source keys.

params: source, target, key | source_key/target_key, source_filter, mode: equal|subset

## `mapping`

Source-to-target transformation rule: for matched keys, target.column == f(source row).

params: source, target (defaults to `dataset`), key | source_key/target_key,
        expression (over source columns), tolerance (numeric), case_insensitive

## `mean_within`

Distribution guard. params: mean, tolerance (absolute)

## `not_null`

No nulls in `column`.

## `null_fraction`

Share of nulls in `column` is at most params `max` (0..1).

## `referential`

Foreign key: non-null values of dataset.column exist in params ref_dataset.ref_column.

## `regex`

Non-null values fully match a regular expression. params: pattern

## `row_count_match`

Target row count equals (filtered) source row count.

params: source, target, source_filter (expression), tolerance (rows), tolerance_pct

## `row_rule`

Every row satisfies a boolean expression. Rows where it evaluates to null pass.

params: expression

## `schema`

Columns exist and non-null values parse as the declared type.

params: columns: {name: type}, strict: bool (no extra columns allowed)

## `unique`

Values (or composite keys) are unique. Single column via `column`, composite via params `columns`.
