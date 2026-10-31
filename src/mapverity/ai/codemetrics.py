"""McCabe and Halstead metrics for Python functions, in the NASA MDP feature vocabulary, so the
defect-risk model can score a team's own transformation code (Glue/PySpark/pandas jobs).

These are approximations of the original MDP tool definitions, documented in the model card:
operators/operands come from Python's tokenizer and AST; cyclomatic complexity counts decision
points; BRANCH_COUNT = 2 x decisions + 1 (consistent with the MDP data). DESIGN_COMPLEXITY and
ESSENTIAL_COMPLEXITY are not computed; use PORTABLE_FEATURES models.
"""

from __future__ import annotations

import ast
import io
import keyword
import math
import token
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Union

import pandas as pd

_DECISIONS = (
    (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.IfExp,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.Assert,
        ast.comprehension,
        ast.match_case,
    )
    if hasattr(ast, "match_case")
    else (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.IfExp,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.Assert,
        ast.comprehension,
    )
)


@dataclass
class FunctionMetrics:
    unit: str
    file: str
    lineno: int
    metrics: Dict[str, float]


def _cyclomatic(node: ast.AST) -> int:
    cc = 1
    for n in ast.walk(node):
        if isinstance(n, _DECISIONS):
            cc += 1
        elif isinstance(n, ast.BoolOp):
            cc += len(n.values) - 1
    return cc


def _halstead(src: str) -> Dict[str, float]:
    ops: List[str] = []
    opnds: List[str] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):
        toks = []
    for t in toks:
        if t.type == token.OP:
            ops.append(t.string)
        elif t.type == token.NAME:
            (ops if keyword.iskeyword(t.string) else opnds).append(t.string)
        elif t.type in (token.NUMBER, token.STRING):
            opnds.append(t.string)
    n1, n2, N1, N2 = len(set(ops)), len(set(opnds)), len(ops), len(opnds)
    vocab, length = n1 + n2, N1 + N2
    volume = length * math.log2(vocab) if vocab > 1 else 0.0
    difficulty = (n1 / 2) * (N2 / n2) if n2 else 0.0
    level = 1 / difficulty if difficulty else 0.0
    effort = difficulty * volume
    return {
        "NUM_OPERATORS": N1,
        "NUM_OPERANDS": N2,
        "NUM_UNIQUE_OPERATORS": n1,
        "NUM_UNIQUE_OPERANDS": n2,
        "HALSTEAD_LENGTH": length,
        "HALSTEAD_VOLUME": volume,
        "HALSTEAD_DIFFICULTY": difficulty,
        "HALSTEAD_LEVEL": level,
        "HALSTEAD_EFFORT": effort,
        "HALSTEAD_PROG_TIME": effort / 18,
        "HALSTEAD_ERROR_EST": volume / 3000,
        "HALSTEAD_CONTENT": volume * level,
    }


def _loc(lines: List[str]) -> Dict[str, float]:
    total = len(lines)
    comments = code_and_comment = blank = 0
    for line in lines:
        s = line.strip()
        if not s:
            blank += 1
        elif s.startswith("#"):
            comments += 1
        elif "#" in s:
            code_and_comment += 1
    return {
        "LOC_TOTAL": total,
        "LOC_COMMENTS": comments,
        "LOC_CODE_AND_COMMENT": code_and_comment,
        "LOC_EXECUTABLE": total - comments - blank,
    }


def function_metrics(source: str, filename: str = "<string>") -> List[FunctionMetrics]:
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            body = lines[node.lineno - 1 : end]
            m: Dict[str, float] = {}
            m.update(_loc(body))
            cc = _cyclomatic(node)
            m["CYCLOMATIC_COMPLEXITY"] = cc
            m["BRANCH_COUNT"] = 2 * (cc - 1) + 1
            m.update(_halstead("\n".join(body)))
            out.append(FunctionMetrics(node.name, filename, node.lineno, m))
    return out


def metrics_frame(paths: Union[str, Path, List[Union[str, Path]]]) -> pd.DataFrame:
    """One row per function across the given files/directories; `unit` = module:function."""
    if not isinstance(paths, list):
        paths = [paths]
    rows = []
    for p in paths:
        p = Path(p)
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            for fm in function_metrics(f.read_text(encoding="utf-8"), str(f)):
                rows.append({"unit": f"{f.stem}:{fm.unit}", "file": str(f), "lineno": fm.lineno, **fm.metrics})
    return pd.DataFrame(rows)
