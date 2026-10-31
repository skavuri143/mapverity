"""Readers for pipeline sources and targets: local files, Amazon S3, and DocumentDB/MongoDB.

boto3 and pymongo are optional: install with `pip install 'mapverity[aws]'`.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..validation.documents import flatten_documents

DOCUMENT_FORMATS = {"documents", "docdb", "mongodb"}


# --------------------------------------------------------------------------- byte sources


def _s3_client():
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError("reading s3:// paths needs boto3: pip install 'mapverity[aws]'") from exc
    return boto3.client("s3", endpoint_url=os.environ.get("MAPVERITY_S3_ENDPOINT") or None)


def _split_s3(uri: str):
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key


def list_objects(location: str) -> List[str]:
    """Expand a prefix/directory into the files under it (non-empty, sorted)."""
    if location.startswith("s3://"):
        bucket, prefix = _split_s3(location)
        if not prefix.endswith("/") and "." in prefix.rsplit("/", 1)[-1]:
            return [location]
        client = _s3_client()
        out = []
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"].rsplit("/", 1)[-1]
                if obj["Size"] and not name.startswith(("_", ".")):
                    out.append(f"s3://{bucket}/{obj['Key']}")
        return sorted(out)
    p = Path(location)
    if p.is_dir():
        return sorted(str(f) for f in p.rglob("*") if f.is_file() and not f.name.startswith(("_", ".")))
    return [location]


def read_bytes(location: str) -> bytes:
    if location.startswith("s3://"):
        bucket, key = _split_s3(location)
        return _s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(location).read_bytes()


# --------------------------------------------------------------------------- tabular formats


def _read_one(raw: bytes, fmt: str, options: Dict[str, Any]) -> pd.DataFrame:
    opts = dict(options)
    if fmt in ("csv", "txt", "delimited", "psv", "tsv"):
        sep = opts.pop("sep", {"psv": "|", "tsv": "\t"}.get(fmt, ","))
        opts.setdefault("dtype", str)  # keep codes like 00123 intact; checks parse types themselves
        opts.setdefault("keep_default_na", False)
        opts.setdefault("na_values", [""])
        return pd.read_csv(io.BytesIO(raw), sep=sep, encoding=opts.pop("encoding", "utf-8"), **opts)
    if fmt in ("fixed_width", "fwf"):
        if "layout" in opts:
            from ..validation.fixedwidth import read_fixed_width

            return read_fixed_width(raw, opts["layout"], encoding=opts.get("encoding", "utf-8"))
        if "copybook" in opts:
            from ..validation.cobol import read_cobol

            return read_cobol(raw, opts["copybook"], encoding=opts.get("encoding", "ascii"), line_sequential=True)
        widths, names = opts.pop("widths"), opts.pop("names")
        df = pd.read_fwf(
            io.BytesIO(raw),
            widths=widths,
            names=names,
            dtype=str,
            encoding=opts.pop("encoding", "utf-8"),
            keep_default_na=False,
            na_values=[""],
            **opts,
        )
        return df
    if fmt in ("cobol", "ebcdic", "mainframe"):
        from ..validation.cobol import read_cobol

        return read_cobol(
            raw, opts["copybook"], encoding=opts.get("encoding", "cp037"), line_sequential=opts.get("line_sequential")
        )
    if fmt == "parquet":
        return pd.read_parquet(io.BytesIO(raw), **opts)
    if fmt in ("json", "jsonl", "ndjson"):
        return pd.json_normalize(_parse_json_docs(raw))
    raise ValueError(f"unsupported format '{fmt}'")


def _parse_json_docs(raw: bytes) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8").strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _infer_format(location: str) -> str:
    if location.startswith(("mongodb://", "mongodb+srv://", "docdb://")):
        return "docdb"
    if location.startswith(("http://", "https://")):
        return "rest"
    if "://" in location.split("?")[0] and location.split("://")[0].split("+")[0] in (
        "postgresql",
        "postgres",
        "mysql",
        "mssql",
        "oracle",
        "sqlite",
        "redshift",
        "snowflake",
    ):
        return "sql"
    name = location.rstrip("/").rsplit("/", 1)[-1].lower()
    for ext, fmt in (
        (".csv", "csv"),
        (".psv", "psv"),
        (".tsv", "tsv"),
        (".txt", "csv"),
        (".dat", "cobol"),
        (".ebc", "cobol"),
        (".parquet", "parquet"),
        (".jsonl", "jsonl"),
        (".ndjson", "jsonl"),
        (".json", "json"),
    ):
        if name.endswith(ext):
            return fmt
    raise ValueError(f"cannot infer format of '{location}'; set `format`")


def read_table(location: str, fmt: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """Read a file, an S3 object, or every object under an S3 prefix / local directory."""
    options = options or {}
    files = list_objects(location)
    if not files:
        raise FileNotFoundError(f"no data found at {location}")
    fmt = (fmt or _infer_format(files[0])).lower()
    frames = [_read_one(read_bytes(f), fmt, options) for f in files]
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


# --------------------------------------------------------------------------- documents


def fetch_documents(location: str, options: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Load documents from a DocumentDB/MongoDB collection or from a JSON/JSONL export."""
    options = options or {}
    if location.startswith(("mongodb://", "mongodb+srv://", "docdb://")):
        try:
            from pymongo import MongoClient  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ImportError("reading DocumentDB needs pymongo: pip install 'mapverity[aws]'") from exc
        uri = os.path.expandvars(location.replace("docdb://", "mongodb://", 1))
        kwargs: Dict[str, Any] = {}
        if options.get("tls_ca_file"):
            # Amazon DocumentDB: TLS with the RDS CA bundle, no retryable writes
            kwargs.update(tls=True, tlsCAFile=options["tls_ca_file"], retryWrites=False)
        client = MongoClient(uri, **kwargs)
        coll = client[options["database"]][options["collection"]]
        cursor = coll.find(options.get("query", {}), options.get("projection"))
        if options.get("limit"):
            cursor = cursor.limit(int(options["limit"]))
        return list(cursor)
    docs: List[Dict[str, Any]] = []
    for f in list_objects(location):
        docs.extend(_parse_json_docs(read_bytes(f)))
    return docs


def read_documents(location: str, name: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, pd.DataFrame]:
    options = options or {}
    docs = fetch_documents(location, options)
    return flatten_documents(docs, name=name, key=options.get("key"))


def read_any(
    name: str, location: Union[str, Path], fmt: Optional[str] = None, options: Optional[Dict[str, Any]] = None
) -> Dict[str, pd.DataFrame]:
    """Read a dataset; document collections and API responses expand into several tables."""
    location = str(location)
    options = options or {}
    fmt = (fmt or _infer_format(location)).lower()
    if fmt in DOCUMENT_FORMATS:
        return read_documents(location, name, options)
    if fmt == "rest":
        from .rest import fetch_rest

        return flatten_documents(fetch_rest(location, options), name=name, key=options.get("key"))
    if fmt == "sql":
        from .sql import read_sql

        return {name: read_sql(location, options)}
    if fmt in ("fixed_width", "fwf") and options.get("multi_record"):
        from ..validation.fixedwidth import read_fixed_width_tables

        out: Dict[str, pd.DataFrame] = {}
        for f in list_objects(location):
            tables = read_fixed_width_tables(
                read_bytes(f),
                options["layout"],
                encoding=options.get("encoding", "utf-8"),
                type_field=options.get("type_field"),
            )
            for rtype, df in tables.items():
                key = f"{name}.{rtype}"
                out[key] = pd.concat([out[key], df], ignore_index=True) if key in out else df
        return out
    return {name: read_table(location, fmt, options)}
