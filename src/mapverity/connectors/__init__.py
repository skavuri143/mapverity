"""Readers for pipeline sources and targets: local files, Amazon S3, DocumentDB/MongoDB, SQL
databases and REST APIs. Cloud and database drivers are optional extras."""

from .storage import DOCUMENT_FORMATS, fetch_documents, list_objects, read_any, read_bytes, read_documents, read_table

__all__ = [
    "DOCUMENT_FORMATS",
    "fetch_documents",
    "list_objects",
    "read_any",
    "read_bytes",
    "read_documents",
    "read_table",
]
