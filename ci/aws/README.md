# Running mapverity in AWS CodePipeline

`pipeline.yaml` creates a CodePipeline (V2) that tests the S3 → Glue → DocumentDB pipeline every
time someone pushes a change to the Glue job, the data-model mapping sheet, a copybook, the
framework or the tests.

```
 push (glue_job.py, mapping/**, copybooks/**, src/**, tests/**, ci/**)
   │
   ▼
 Source ──► UnitTests ──────────────► DeployToTest ─────► IntegrationTests ───────────────► Approve
            · framework unit tests    · upload Glue        · fixtures → S3 (isolated prefix)   (manual)
            · mapping → checks          script and         · run the real Glue job
            · local end-to-end run      copybooks          · validate DocumentDB vs mapping
            · mutation-score gate                          · JUnit → CodeBuild Reports
```

Each stage publishes JUnit XML to **CodeBuild → Report groups**, so failed checks show the
document key, the actual value and the expected value. Any failure stops the pipeline before
the approval step.

## Prerequisites

| You need | Why |
|---|---|
| A CodeConnections (CodeStar) connection to your Git provider | source + file-path triggers |
| A **test** DocumentDB cluster and a Secrets Manager secret `{"host","port","username","password"}` | integration target; never point CI at production |
| A Glue NETWORK connection into that VPC, and a Glue job role | the Glue job writes to DocumentDB |
| Private subnets with a NAT route, and a security group the cluster accepts on 27017 | the integration CodeBuild project runs in the VPC |
| The Cobrix (spark-cobol) JARs in S3 | the Glue job reads EBCDIC with Cobrix |

## Deploy

```bash
aws cloudformation deploy \
  --stack-name member-etl-ci \
  --template-file ci/aws/pipeline.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      ConnectionArn=arn:aws:codeconnections:us-east-1:111122223333:connection/abc \
      FullRepositoryId=your-org/your-repo \
      VpcId=vpc-0123 PrivateSubnetIds=subnet-a,subnet-b \
      DocDbClientSecurityGroupId=sg-0123 \
      DocDbSecretArn=arn:aws:secretsmanager:us-east-1:111122223333:secret:ci/docdb/test-AbCd \
      GlueRoleArn=arn:aws:iam::111122223333:role/glue-member-etl \
      GlueConnectionName=docdb-test-vpc \
      CobrixJars=s3://my-jars/spark-cobol_2.12-2.7.x.jar,s3://my-jars/cobol-parser_2.12-2.7.x.jar
```

## Using it for your own pipeline

1. Replace `examples/member_eligibility/glue_job.py`, the copybooks and the mapping sheet with your
   own (or change the paths in the three buildspecs and in the pipeline's `FilePaths` trigger).
2. Replace `generate_data.py` with a fixture generator for your source layouts. Keep fixtures
   **synthetic**: never copy production member data into CI.
3. In `run_integration.py`, set the dataset definitions for your sources and target collection.
4. Tune the mutation gate in `buildspec-unit.yml` (`--min-score`) once you have a baseline.

## Safety rails built in

- Fixtures go to a per-build prefix (`ci-runs/<build-number>/`) that expires after 14 days.
- `run_integration.py` refuses to empty a database or collection without `test` in its name.
- Credentials come from Secrets Manager at run time and are never written to disk or logs.
- The CodeBuild role can start only the test Glue job and read only the test secret.

## Prefer GitHub Actions?

`.github/workflows/ci.yml` runs the same unit stage on every push and pull request. For the
integration stage, run `ci/aws/run_integration.py` from a self-hosted runner inside the VPC,
with an OIDC role.
