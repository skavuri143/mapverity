"""pytest plugin: every check in a `*.mapverity.yaml` file becomes a pytest test.

    pytest tests/                       # collects tests/**/*.mapverity.yaml
    pytest -k "map:members"             # run only mapping checks for the members collection

Data is loaded once per spec file.
"""

from __future__ import annotations

import json

import pytest

from .core.engine import load_data, run_check
from .core.spec import Spec
from .validation.checks import Context


def pytest_collect_file(parent, file_path):
    if file_path.name.endswith(".mapverity.yaml") or file_path.name.endswith(".mapverity.yml"):
        return SpecFile.from_parent(parent, path=file_path)
    return None


class SpecFile(pytest.File):
    def collect(self):
        spec = Spec.load(self.path)
        self._spec = spec
        self._ctx = None
        for check in spec.checks:
            yield CheckItem.from_parent(self, name=check.id, check=check)

    def context(self) -> Context:
        if self._ctx is None:
            self._ctx = Context(load_data(self._spec))
        return self._ctx


class CheckFailure(Exception):
    pass


class CheckItem(pytest.Item):
    def __init__(self, *, check, **kwargs):
        super().__init__(**kwargs)
        self.check = check
        self.add_marker(pytest.mark.mapverity)

    def runtest(self):
        result = run_check(self.parent.context(), self.check)
        if result.status == "error":
            raise CheckFailure(f"check error: {result.error}")
        if not result.passed:
            sample = json.dumps(result.outcome.sample, default=str, indent=2)
            raise CheckFailure(f"{result.outcome.message}\nsample: {sample}")

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, CheckFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, None, f"mapverity: {self.check.id}"


def pytest_configure(config):
    config.addinivalue_line("markers", "mapverity: a check generated from a mapverity spec file")
