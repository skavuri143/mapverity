# Mutation testing for data

A suite that has never failed proves little. `mapverity mutate` copies the pipeline's output,
injects one realistic fault at a time, and reruns the checks. A mutant is **killed** when a check
that passed on the original output fails on the mutant. The **mutation score** is killed /
non-equivalent mutants; the **survivors** list the suite's blind spots, column by column.

| Fault class | Operators (what they model) |
|---|---|
| completeness | `null_injection` (broken lookup), `row_drop` (bad join or filter), `default_value` (placeholder fallback) |
| uniqueness | `row_duplicate` (wrong upsert key) |
| accuracy | `numeric_shift` (unit/scale), `off_by_one`, `date_shift` (timezone/epoch), `value_swap` (misaligned join), `column_swap` (SELECT order) |
| consistency | `truncate` (narrow column), `case_change`, `whitespace` (CHAR padding), `date_format` (locale) |
| validity | `type_corruption` (encoding), `out_of_domain` (new code) |

```bash
mapverity mutate member.mapverity.yaml --dataset members --rate 0.05 --json mutation.json --min-score 0.9
```

`--rate` is the share of rows a mutant touches; `--min-score` makes the command a CI gate, which
stops a suite from silently weakening when someone deletes rules from the mapping sheet.

Survivors on fields the mapping marks as constraint-only (for example a load timestamp the job
sets at run time) are expected: there is nothing in the source to derive them from.
