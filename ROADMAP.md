# Roadmap

Priorities are set with users; open an issue to propose changes.

## Now (0.1.x)
- Stabilize the spec format and mapping-sheet vocabulary based on early feedback
- More example pipelines (provider enrollment, pharmacy claims, banking transactions)
- Published documentation site

## Next (0.2)
- COBOL `OCCURS DEPENDING ON`, `SIGN SEPARATE`, COMP-1/COMP-2
- Incremental validation of only the records a run changed
- Spark-native execution for very large outputs (validate where the data lives)
- Targets: DynamoDB, BigQuery, Snowflake examples

## Later
- Selected, documented subsets of healthcare X12 (834/837) validation; no claim of full compliance
- Evaluation of the LLM rule-proposal step on public pipelines
- Studies with independent adopters on production pipelines
