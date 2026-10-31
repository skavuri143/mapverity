"""Read a table or query from any SQLAlchemy-supported database (PostgreSQL, Oracle, SQL Server,
Redshift, Snowflake, SQLite, ...). Requires `pip install 'mapverity[sql]'` plus the database driver.

    datasets:
      claims_stage:
        path: postgresql+psycopg://${PGUSER}:${PGPASSWORD}@host:5432/claims
        format: sql
        options:
          query: "select * from stage.claim_header where load_date = current_date"
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pandas as pd


def read_sql(url: str, options: Dict[str, Any]) -> pd.DataFrame:
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError("format 'sql' needs SQLAlchemy: pip install 'mapverity[sql]'") from exc
    query: Optional[str] = options.get("query")
    table: Optional[str] = options.get("table")
    if not query and not table:
        raise ValueError("format 'sql' needs options.query or options.table")
    if table and not query:
        if not all(part.replace("_", "").isalnum() for part in str(table).split(".")):
            raise ValueError(f"invalid table name {table!r}")
        query = f"select * from {table}"  # noqa: S608 - identifier validated above
    if query is None:  # unreachable: one of query/table is set above
        raise ValueError("format 'sql' needs options.query or options.table")
    engine = create_engine(os.path.expandvars(url))
    try:
        with engine.connect() as conn:
            return pd.read_sql_query(text(query), conn, params=options.get("params"))
    finally:
        engine.dispose()
