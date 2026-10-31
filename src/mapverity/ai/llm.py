"""Optional LLM assistance: propose extra business rules, then keep only the ones the data supports.

mapverity never trusts a model's output directly. Proposed checks are parsed, executed against
known-good data, and accepted only if they pass; optionally they must also kill at least one
mutant the current spec misses. Any chat-completion backend works: pass a function
`complete(prompt: str) -> str`.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd
import yaml

from ..core.engine import run_check
from ..core.spec import CheckSpec, Spec
from ..validation.checks import REGISTRY, Context

PROMPT = """You are a data quality engineer. A data pipeline produces the dataset(s) profiled below.
An existing test spec is shown. Propose up to {n} ADDITIONAL checks that encode business rules
a domain expert would expect (cross-field consistency, conditional requirements, plausible
ranges, code relationships). Use only these check types: {types}.
Expressions may use: col("x"), date(), num(), upper(), lower(), strip(), isnull(), notnull(),
isin(x, [..]), lookup(x, {{..}}, default), years_between(a, b), where(cond, a, b), & | ~.
Answer with a YAML list of checks only, no prose.

## Profile
{profile}

## Existing spec
{spec}
"""


def profile_text(data: Dict[str, pd.DataFrame], max_values: int = 8) -> str:
    lines = []
    for name, df in data.items():
        lines.append(f"dataset {name}: {len(df)} rows")
        for c in df.columns:
            s = df[c]
            sample = s.dropna().astype(str).unique()[:max_values].tolist()
            lines.append(f"  - {c}: nulls={int(s.isna().sum())}, distinct={s.nunique()}, e.g. {sample}")
    return "\n".join(lines)


@dataclass
class Suggestion:
    check: CheckSpec
    accepted: bool
    reason: str


def suggest_checks(
    spec: Spec, data: Dict[str, pd.DataFrame], complete: Callable[[str], str], n: int = 10
) -> List[Suggestion]:
    prompt = PROMPT.format(n=n, types=", ".join(sorted(REGISTRY)), profile=profile_text(data), spec=spec.to_yaml())
    raw = complete(prompt).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else ""
    try:
        items = yaml.safe_load(raw) or []
    except yaml.YAMLError as exc:
        return [Suggestion(CheckSpec("invalid"), False, f"unparseable model output: {exc}")]
    ctx = Context(dict(data))
    out = []
    for item in items if isinstance(items, list) else []:
        try:
            check = CheckSpec.from_dict(item)
        except Exception as exc:
            out.append(Suggestion(CheckSpec("invalid"), False, str(exc)))
            continue
        res = run_check(ctx, check)
        if res.status == "error":
            out.append(Suggestion(check, False, f"error: {res.error}"))
        elif not res.passed:
            out.append(Suggestion(check, False, f"fails on known-good data: {res.outcome.message}"))
        else:
            out.append(Suggestion(check, True, "passes on known-good data"))
    return out


def openai_compatible(
    model: str, base_url: Optional[str] = None, api_key_env: str = "OPENAI_API_KEY", timeout: int = 120
) -> Callable[[str], str]:
    """A minimal client for any OpenAI-compatible /chat/completions endpoint (incl. local servers)."""
    base = (base_url or os.environ.get("MAPVERITY_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

    def complete(prompt: str) -> str:
        body = json.dumps(
            {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        ).encode()
        if not base.startswith(("https://", "http://")):
            raise ValueError("LLM base URL must be http(s)")
        req = urllib.request.Request(  # noqa: S310 - scheme checked above
            f"{base}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get(api_key_env, '')}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured endpoint
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    return complete
