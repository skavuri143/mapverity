# Getting started

This walkthrough uses the synthetic health-benefits member eligibility pipeline in `examples/member_eligibility`:
an EBCDIC member master file described by a COBOL copybook, a pipe-delimited eligibility file,
and member JSON documents with a nested eligibility array (what a Glue job would write to
DocumentDB). Every record is generated.

## 1. Install and generate data

```bash
pip install "mapverity[all]"
cd examples/member_eligibility
python generate_data.py      # landing/MEMBER.MASTER.dat (EBCDIC) and landing/ELIG_SEGMENTS.txt
python transform.py          # output/members.jsonl: the pipeline under test
```

## 2. Compile the mapping sheet into checks

```bash
mapverity from-mapping mapping/member_data_model_mapping.csv --datasets datasets.yaml -o member.mapverity.yaml
```

`datasets.yaml` says where each dataset lives; the mapping sheet says how each target field is
derived. The output is an ordinary YAML spec you can review and commit.

## 3. Validate

```bash
mapverity validate member.mapverity.yaml --html report.html --junit results.xml
```

## 4. Break it on purpose

```bash
python transform.py --defect D13_segments_inner_join_drops_last
mapverity validate member.mapverity.yaml       # exit code 1; the report names the failing members
python transform.py                            # restore
```

`transform.py --help` lists 20 seeded defects; every one is caught.

## 5. Measure the suite and gate the release

```bash
mapverity mutate member.mapverity.yaml --dataset members --json mutation.json
mapverity coverage member.mapverity.yaml --mapping mapping/member_data_model_mapping.csv
mapverity validate member.mapverity.yaml --mapping mapping/member_data_model_mapping.csv \
    --mutation-report mutation.json --gates gates.yaml --html report.html
```

## 6. Run it in pytest

Every check in a `*.mapverity.yaml` file is collected as a pytest test:

```bash
pytest member.mapverity.yaml -k "map:members"
```

## Next

- Second domain: `examples/insurance_claims` (multi-record fixed-width claims with control totals).
- The same pipeline against a live MongoDB/DocumentDB: `examples/docker_demo`.
- On AWS: `ci/aws/` runs the real Glue job on fixtures and validates DocumentDB in CodePipeline.
