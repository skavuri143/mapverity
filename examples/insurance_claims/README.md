# Insurance claims ingestion

```text
landing/CLAIMS.<date>.txt  HDR / CLM / LIN... / TRL  (layouts/CLAIMS-FILE.layout.csv) ─┐
reference/policies.csv                                                                  ├─► transform.py ─► output/claims.jsonl
```

```bash
python generate_data.py
mapverity validate file_controls.mapverity.yaml        # inbound file: trailer totals, orphan lines, line sums
python transform.py
mapverity from-mapping mapping/claims_mapping.csv --datasets datasets.yaml -o claims.mapverity.yaml
mapverity validate claims.mapverity.yaml --html report.html
python transform.py --defect C05_reversal_sign_lost && mapverity validate claims.mapverity.yaml
```

Covers signed amounts with implied decimals, reversals, code translations, ICD-10-CM dot
formatting, CMS place-of-service codes, policy foreign keys and nested service lines. Procedure
and diagnosis codes come from a short list of common public codes; everything else is generated.
