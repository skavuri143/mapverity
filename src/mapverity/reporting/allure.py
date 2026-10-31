"""Write Allure result files (one JSON per check), so MapVerity runs appear in an existing Allure
report and its history alongside UI and API tests."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Union

from ..core.engine import ValidationResult

_STATUS = {"passed": "passed", "failed": "failed", "error": "broken"}


def write_allure_results(result: ValidationResult, directory: Union[str, Path], suite: str = "MapVerity") -> int:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    stop = int(time.time() * 1000)
    for r in result.results:
        start = stop - int(r.seconds * 1000)
        body = {
            "uuid": str(uuid.uuid4()),
            "historyId": f"{result.spec_name}:{r.check.id}",
            "name": r.check.id,
            "fullName": f"{result.spec_name}.{r.check.id}",
            "status": _STATUS.get(r.status, "unknown"),
            "statusDetails": {
                "message": r.error or r.outcome.message,
                "trace": json.dumps(r.outcome.sample, indent=1, default=str) if r.outcome.sample else "",
            },
            "stage": "finished",
            "start": start,
            "stop": stop,
            "labels": [
                {"name": "suite", "value": suite},
                {"name": "parentSuite", "value": result.spec_name},
                {"name": "subSuite", "value": r.check.dataset or "data"},
                {"name": "tag", "value": r.check.type},
                {"name": "severity", "value": "critical" if r.check.severity == "error" else "minor"},
            ],
            "parameters": [
                {"name": "rows", "value": str(r.outcome.total)},
                {"name": "failed", "value": str(r.outcome.failed)},
            ],
        }
        (out / f"{body['uuid']}-result.json").write_text(json.dumps(body), encoding="utf-8")
    return len(result.results)
