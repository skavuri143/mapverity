"""Regenerate docs/checks.md from the check registry: python docs/gen_checks.py"""

import inspect
from pathlib import Path

from mapverity.validation.checks import REGISTRY

lines = [
    "# Checks reference",
    "",
    "Generated from the code by `docs/gen_checks.py`; a test fails if a registered check is missing here.",
    "",
    "Every check accepts `type`, `name` (optional id), `dataset`, `column` (when relevant) and",
    "`severity: error | warn`. Other keys are the check's parameters.",
    "",
]
for name in sorted(REGISTRY):
    doc = inspect.getdoc(REGISTRY[name]) or ""
    lines += [f"## `{name}`", "", doc, ""]
Path(__file__).with_name("checks.md").write_text("\n".join(lines), encoding="utf-8")
print(f"documented {len(REGISTRY)} checks")
