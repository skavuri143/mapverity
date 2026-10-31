"""Read JSON from a REST API into documents (then tables), so API responses can be validated with
the same checks as pipeline output. Requires `pip install 'mapverity[api]'` (httpx).

    datasets:
      members_api:
        path: https://api.example.org/v1/members?updatedSince=2026-10-01
        format: rest
        options:
          records: data.items            # where the list of records sits in the response
          next: links.next               # follow pagination links (optional)
          headers: {Authorization: "Bearer ${MEMBERS_API_TOKEN}"}
          key: memberId
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _dig(obj: Any, path: Optional[str]) -> Any:
    if not path:
        return obj
    for part in path.split("."):
        if obj is None:
            return None
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def fetch_rest(url: str, options: Dict[str, Any], client: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Return every record across pages. `client` is injectable for tests (an httpx.Client)."""
    owns = client is None
    if client is None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ImportError("format 'rest' needs httpx: pip install 'mapverity[api]'") from exc
        client = httpx.Client(timeout=float(options.get("timeout", 30)), follow_redirects=True)
    headers = {k: os.path.expandvars(str(v)) for k, v in (options.get("headers") or {}).items()}
    max_pages = int(options.get("max_pages", 1000))
    records: List[Dict[str, Any]] = []
    next_url: Optional[str] = os.path.expandvars(url)
    try:
        for _ in range(max_pages):
            if not next_url:
                break
            resp = client.get(next_url, headers=headers, params=options.get("params"))
            resp.raise_for_status()
            body = resp.json()
            page = _dig(body, options.get("records"))
            if isinstance(page, dict):
                page = [page]
            records.extend(page or [])
            next_url = _dig(body, options.get("next")) if options.get("next") else None
    finally:
        if owns:
            client.close()
    return records
