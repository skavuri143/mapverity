"""S3 and DocumentDB code paths, exercised against emulators (moto, mongomock).

Skipped automatically when the optional test dependencies are not installed:
    pip install "moto[s3]" mongomock boto3 pymongo
"""

import sys
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
mongomock = pytest.importorskip("mongomock")

from mapverity import spec_from_mapping, validate  # noqa: E402
from mapverity.connectors import fetch_documents, read_table  # noqa: E402
from mapverity.core.spec import DatasetSpec  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "member_eligibility"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="landing")
        yield client


def test_read_s3_object_and_prefix(s3):
    s3.put_object(Bucket="landing", Key="elig/part-1.psv", Body=b"MBR_ID|SEQ\nM1|1\n")
    s3.put_object(Bucket="landing", Key="elig/part-2.psv", Body=b"MBR_ID|SEQ\nM2|1\nM2|2\n")
    s3.put_object(Bucket="landing", Key="elig/_SUCCESS", Body=b"")
    df = read_table("s3://landing/elig/", "psv")
    assert df["MBR_ID"].tolist() == ["M1", "M2", "M2"]
    one = read_table("s3://landing/elig/part-1.psv")
    assert len(one) == 1


def test_documentdb_reader_with_mongomock(monkeypatch):
    import pymongo

    client = mongomock.MongoClient()
    client["hub"]["members"].insert_many(
        [{"_id": "M1", "memberId": "M1", "elig": [{"seq": 1}]}, {"_id": "M2", "memberId": "M2", "elig": []}]
    )
    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **k: client)
    docs = fetch_documents(
        "docdb://u:p@host:27017/", {"database": "hub", "collection": "members", "query": {"memberId": "M1"}}
    )
    assert [d["_id"] for d in docs] == ["M1"]


def test_full_pipeline_through_s3_and_documentdb(s3, monkeypatch, tmp_path):
    """Sources in S3, documents in (mock) DocumentDB, checks compiled from the mapping sheet."""
    import pymongo

    sys.path.insert(0, str(EXAMPLE))
    import generate_data
    import transform as T

    generate_data.generate(200, 5, tmp_path)
    for f in tmp_path.iterdir():
        s3.upload_file(str(f), "landing", f"run-1/{f.name}")
    master, elig = T.read_sources(tmp_path)
    client = mongomock.MongoClient()
    client["hub_test"]["members_test"].insert_many(T.transform(master, elig))
    monkeypatch.setattr(pymongo, "MongoClient", lambda *a, **k: client)

    datasets = {
        "member_master": DatasetSpec(
            path="s3://landing/run-1/MEMBER.MASTER.dat",
            format="cobol",
            options={"copybook": str(EXAMPLE / "copybooks" / "MEMBER-REC.cpy")},
        ),
        "elig_segments": DatasetSpec(path="s3://landing/run-1/ELIG_SEGMENTS.txt", format="psv"),
        "members": DatasetSpec(
            path="docdb://u:p@host:27017/",
            format="docdb",
            options={"database": "hub_test", "collection": "members_test", "key": "memberId"},
        ),
    }
    spec = spec_from_mapping(EXAMPLE / "mapping" / "member_data_model_mapping.csv", datasets=datasets)
    result = validate(spec)
    assert result.passed, [r.to_dict() for r in result.failures]

    # a broken Glue run is caught end to end
    client["hub_test"]["members_test"].update_many({}, {"$set": {"countyCode": "7"}})
    result = validate(spec)
    assert any(r.check.id == "map:members:countyCode" for r in result.failures)
