# Sources and targets

A spec's `datasets:` section says where data lives. Paths may be local files, directories, S3
objects or prefixes (`s3://bucket/prefix/`), and `${ENV_VARS}` are expanded at run time.

| `format` | Reads | Notes |
|---|---|---|
| `cobol` | EBCDIC/ASCII files described by a copybook | `options: {copybook, encoding: cp037}`; COMP-3, binary, zoned decimal, OCCURS, REDEFINES |
| `fixed_width` | layout-described flat files | `options: {layout}`; multi-record files: `multi_record: true, type_field` → `<name>.<TYPE>` tables |
| `csv`, `psv`, `tsv` | delimited text | read as strings, so codes keep leading zeros |
| `json`, `jsonl`, `parquet` | | |
| `documents` | JSON/JSONL or `mongoexport` files | flattened: nested objects → dotted columns, arrays → child tables |
| `docdb` | DocumentDB / MongoDB collection | `options: {database, collection, key, query, tls_ca_file}` |
| `sql` | any SQLAlchemy URL | `options: {query, params}` or `{table}`; install your driver |
| `rest` | paginated JSON API | `options: {records, next, headers, key}` |

## Document flattening

```text
{"memberId": "M1", "name": {"first": "Ana"}, "eligibility": [{"seq": 1}, {"seq": 2}]}

members              memberId | name.first | eligibility._count
members.eligibility  memberId | _index | seq
```

Child rows carry their ancestors' keys, so array elements join back to source records with a
composite key, and `<array>._count` lets you check that no element was lost.

## Amazon DocumentDB

```yaml
members:
  path: docdb://${DOCDB_USER}:${DOCDB_PASS}@${DOCDB_HOST}:27017/?replicaSet=rs0&readPreference=secondaryPreferred
  format: docdb
  options: {database: member_hub, collection: members, key: memberId, tls_ca_file: global-bundle.pem}
```

Run from inside the cluster's VPC (CodeBuild in VPC, a bastion, or a self-hosted runner).

## API contracts

`mapverity from-openapi openapi.yaml --schema Member --dataset members_api` compiles an OpenAPI 3
schema (required, types, enums, patterns, ranges, lengths, nested objects, arrays) into checks for
responses read with the `rest` format.
