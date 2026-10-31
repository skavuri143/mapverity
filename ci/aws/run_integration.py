"""Integration test for the deployed Glue job, run by CodeBuild in the pipeline's test stage.

  1. generate deterministic, synthetic source files (fixtures) for this build
  2. upload them to an isolated S3 prefix: s3://<landing-bucket>/ci-runs/<run-id>/
  3. empty the TEST DocumentDB collection (refuses any collection without "test" in its name)
  4. run the real Glue job against the fixtures and wait for it to finish
  5. compile the data-model mapping into checks and validate S3 sources vs DocumentDB documents
  6. write JUnit XML for CodeBuild Reports; exit non-zero on any failure so the pipeline stops

Requires: mapverity[aws]; AWS credentials from the CodeBuild role; network access to DocumentDB
(run the CodeBuild project inside the cluster's VPC).

Example:
  python ci/aws/run_integration.py --glue-job member-to-docdb-test \
      --landing-bucket my-test-landing --docdb-secret-id ci/docdb/test \
      --database member_hub_test --collection members_test --tls-ca-file global-bundle.pem
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "member_eligibility"
sys.path.insert(0, str(EXAMPLE))

from mapverity import validate  # noqa: E402
from mapverity.core.engine import load_data  # noqa: E402
from mapverity.core.spec import DatasetSpec  # noqa: E402
from mapverity.reporting.formats import to_junit, to_markdown  # noqa: E402
from mapverity.validation.mapping import load_mapping, mapping_coverage, spec_from_mapping  # noqa: E402

TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}


def log(msg: str) -> None:
    print(f"[integration] {msg}", flush=True)


def upload_fixtures(bucket: str, prefix: str, members: int, seed: int, workdir: Path) -> None:
    import generate_data

    generate_data.generate(members, seed, workdir)
    s3 = boto3.client("s3")
    for f in sorted(workdir.iterdir()):
        s3.upload_file(str(f), bucket, f"{prefix}/{f.name}")
        log(f"uploaded s3://{bucket}/{prefix}/{f.name}")


def reset_collection(uri: str, database: str, collection: str, tls_ca_file: str) -> None:
    if "test" not in collection.lower() or "test" not in database.lower():
        raise SystemExit(
            f"refusing to empty '{database}.{collection}': database and collection names must contain 'test'"
        )
    from pymongo import MongoClient

    client = MongoClient(uri, tls=True, tlsCAFile=tls_ca_file, retryWrites=False)
    deleted = client[database][collection].delete_many({}).deleted_count
    log(f"emptied {database}.{collection} ({deleted} documents)")


def run_glue(job: str, arguments: dict, timeout_s: int) -> str:
    glue = boto3.client("glue")
    run_id = glue.start_job_run(JobName=job, Arguments=arguments)["JobRunId"]
    log(f"started Glue job {job} run {run_id}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        jr = glue.get_job_run(JobName=job, RunId=run_id, PredecessorsIncluded=False)["JobRun"]
        state = jr["JobRunState"]
        if state in TERMINAL:
            log(f"Glue run finished: {state} ({jr.get('ExecutionTime', '?')}s)")
            if state != "SUCCEEDED":
                raise SystemExit(f"Glue job failed: {jr.get('ErrorMessage', state)}")
            return run_id
        time.sleep(20)
    glue.batch_stop_job_run(JobName=job, JobRunIds=[run_id])
    raise SystemExit(f"Glue job did not finish within {timeout_s}s; stopped run {run_id}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--glue-job", required=True)
    p.add_argument("--landing-bucket", required=True)
    p.add_argument("--copybook-s3", help="S3 path of the copybook the Glue job reads (default: alongside fixtures)")
    p.add_argument("--docdb-secret-id", required=True, help="Secrets Manager secret with host/port/username/password")
    p.add_argument("--database", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--tls-ca-file", default="global-bundle.pem")
    p.add_argument("--mapping", default=str(EXAMPLE / "mapping" / "member_data_model_mapping.csv"))
    p.add_argument("--copybook", default=str(EXAMPLE / "copybooks" / "MEMBER-REC.cpy"))
    p.add_argument("--members", type=int, default=1000)
    p.add_argument("--seed", type=int, default=20261030)
    p.add_argument("--reports", default="reports")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument(
        "--skip-glue", action="store_true", help="validate the current collection without re-running the job"
    )
    a = p.parse_args()

    run = os.environ.get("CODEBUILD_BUILD_NUMBER") or uuid.uuid4().hex[:8]
    prefix = f"ci-runs/{run}"
    reports = Path(a.reports)
    reports.mkdir(parents=True, exist_ok=True)
    secret = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=a.docdb_secret_id)["SecretString"])
    uri = (
        f"mongodb://{secret['username']}:{secret['password']}@{secret['host']}:{secret.get('port', 27017)}"
        "/?replicaSet=rs0&readPreference=secondaryPreferred"
    )

    if not a.skip_glue:
        upload_fixtures(a.landing_bucket, prefix, a.members, a.seed, Path("fixtures"))
        copybook_s3 = a.copybook_s3 or f"s3://{a.landing_bucket}/{prefix}/MEMBER-REC.cpy"
        if not a.copybook_s3:
            boto3.client("s3").upload_file(a.copybook, a.landing_bucket, f"{prefix}/MEMBER-REC.cpy")
        reset_collection(uri, a.database, a.collection, a.tls_ca_file)
        run_glue(
            a.glue_job,
            {
                "--landing_path": f"s3://{a.landing_bucket}/{prefix}",
                "--copybook_path": copybook_s3,
                "--docdb_secret_id": a.docdb_secret_id,
                "--target_database": a.database,
                "--target_collection": a.collection,
            },
            a.timeout,
        )

    datasets = {
        "member_master": DatasetSpec(
            path=f"s3://{a.landing_bucket}/{prefix}/MEMBER.MASTER.dat",
            format="cobol",
            options={"copybook": a.copybook, "encoding": "cp037"},
        ),
        "elig_segments": DatasetSpec(path=f"s3://{a.landing_bucket}/{prefix}/ELIG_SEGMENTS.txt", format="psv"),
        "members": DatasetSpec(
            path=uri,
            format="docdb",
            options={
                "database": a.database,
                "collection": a.collection,
                "key": "memberId",
                "tls_ca_file": a.tls_ca_file,
            },
        ),
    }
    mappings = load_mapping(a.mapping)
    spec = spec_from_mapping(mappings, name="glue-member-integration", datasets=datasets)
    spec.base_dir = Path.cwd()
    data = load_data(spec)
    log(f"loaded {len(data['member_master'])} source records, {len(data['members'])} documents")
    result = validate(spec, data)
    (reports / "integration.xml").write_text(to_junit(result), encoding="utf-8")
    (reports / "integration.md").write_text(to_markdown(result), encoding="utf-8")
    coverage = mapping_coverage(mappings, data).to_dict()
    (reports / "mapping-coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    print(to_markdown(result))
    log(f"mapping value coverage: {coverage['value_coverage']:.0%}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
