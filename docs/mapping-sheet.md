# Mapping sheet

One row per target field. Columns (case and spacing are ignored; common synonyms are accepted):

| Column | Meaning |
|---|---|
| `target_table` | Target table or document collection. Arrays are child tables: `members.eligibility`. |
| `target_field` | Field; nested objects use dots: `name.first`; array sizes: `eligibility._count`. |
| `source_dataset` | Dataset name from `datasets:`; multi-record files use `claims_file.CLM`. |
| `source_field` | Source column. |
| `rule` | How the value is derived (below). |
| `data_type` | `string`, `integer`, `number`, `date`, `datetime`, `boolean`. |
| `required` | `Y` → `not_null` check. |
| `key` | `Y` on the fields that join target rows to source rows; several rows form a composite key. |
| `allowed_values` | `A|B|C` → domain check. |
| `pattern` | Full-match regex → `regex` check. |
| `references` | `dataset.column` the value must exist in → `referential` check. |
| `source_filter` | On a key row: which source records the pipeline is expected to load (an expression). |
| `tolerance` | Absolute numeric tolerance (engines round differently at .x5). |
| `description` | Free text. |

## Rules

| Rule | Expected value |
|---|---|
| `direct` (or blank) | the source value |
| `trim`, `upper`, `lower`, `title`, `upper_trim` | normalised source string |
| `lookup: M=MALE, F=FEMALE, *=UNKNOWN` | code translation; `*` is the default (omit it to expect null) |
| `date: %Y%m%d` | source date reformatted to ISO `YYYY-MM-DD` |
| `concat: FIRST, LAST` | values joined by one space |
| `constant: LEGACY` | a literal |
| `count` | on `<array>._count`: number of matching source records |
| `ref: dataset.column` | value not derivable, but must exist in a reference dataset |
| `none` | documented, not derivable (shows as constraint-only in coverage) |
| `expr: <expression>` | anything else |

## Expressions

Python expressions over source columns, vectorised: `col("name")`, `num()`, `date()`, `str()`,
`strip()`, `upper()`, `lower()`, `title()`, `length()`, `substr(x, start, len)` (1-based),
`lpad(x, n, "0")`, `rpad()`, `replace()`, `concat(a, b, sep=" ")`, `coalesce()`, `lookup()`,
`to_date(x, fmt)`, `where(cond, a, b)`, `isnull()`, `notnull()`, `isin(x, [...])`, `round()`,
`abs()`, `years_between(a, b)`, and `& | ~` for logic. Example:

```text
expr: round(num(col("mbr_monthly_income")) * 12 / (15060 + 5380 * (num(col("mbr_hh_size")) - 1)) * 100, 1)
```

## Comparison semantics

Strings compare exactly (no trimming, no case folding): padding and casing are common defects.
Numbers compare within `tolerance`. Numeric-looking strings (`"07"`) compare as text. Nulls equal
nulls.
