# FAQ

**Does MapVerity replace Great Expectations, dbt tests or Deequ?**
No. Those validate properties of one dataset. MapVerity checks that each target field was
*derived correctly from its source* according to a mapping sheet, and reads sources such as COBOL
files and nested documents. Many teams will use both.

**Does it need Spark or AWS?**
No. The core runs on pandas on a laptop. S3, DocumentDB, SQL and REST are optional readers.

**How big can the data be?**
It validates in memory: 100,000 member documents with 49 checks take about 8 seconds on 2 vCPUs. For larger outputs, validate a partition or a run's delta, or sample; Spark-native
execution is on the roadmap.

**Is it a compliance tool?**
No. Passing MapVerity checks is not a certification of HIPAA, X12, NAIC or any other standard.

**Can I use real data?**
On your own infrastructure, yes; MapVerity sends data nowhere unless you configure an LLM
endpoint. Failure samples appear in reports, so protect reports like the data. Never attach real
records to GitHub issues.

**Why does `mapverity risk train` need the internet?**
It downloads the public NASA MDP datasets once and verifies their checksums; MapVerity does not
redistribute them.
