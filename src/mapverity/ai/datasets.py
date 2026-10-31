"""Public defect datasets used to train and evaluate the defect-risk model.

MapVerity does not redistribute third-party data. `fetch_nasa_mdp()` downloads the cleaned NASA
Metrics Data Program (MDP) datasets (the D'' versions of Shepperd et al., IEEE TSE 39(9), 2013)
from a public mirror, verifies each file's SHA-256 and caches it locally. The original data was
produced by the NASA IV&V Metrics Data Program; check the terms that apply to your use before
redistributing it. Provenance and checksums are recorded in `docs/data/nasa-mdp.md`.
"""

from __future__ import annotations

import hashlib
import io
import os
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import pandas as pd

MIRROR = "https://raw.githubusercontent.com/klainfo/NASADefectDataset/master/CleanedData/MDP/D%27%27"

# SHA-256 of the D'' files, recorded on 2026-10-30.
NASA_MDP: Dict[str, str] = {
    "CM1": "870f523699f25bb78c1b6dab73961be8380cd73f3fd41fefe961c65048876a79",
    "JM1": "579060380b8d8cb1d9d9414ebf2eb742c9c90faf491ff1182e2c15bb2aaac022",
    "KC1": "5cea7a3e02ff9954a74b3ef29ad895afaacff82a8c9cf63c8b90023d497a4ca8",
    "KC3": "f93b7f373101d9e6390196d50f2e31510bc24474ad40a7609c2b69b43c3afa34",
    "MC1": "d7a9047cf619596946e4c70fbd68053647e66d577791ac861119f7268ee8d732",
    "MC2": "6e837f0e08f3e795f48be7d0a7ef802157a2ccb0ff08d8bacd8b567d438f4479",
    "MW1": "93e86aa4e87d5cafb860e7eb99c1a50346dc6576d9815e190417ab9ae0b54c52",
    "PC1": "9d1dd98f7f121206813cc0be4fcbc9fe4502ca42cca882e697bb300109046f21",
    "PC2": "082bd53f6c16ddf84a1c11f5d3c2f37ad7d45734828de36ff327dd18f0d3be53",
    "PC3": "71e058433f948fe331326d2d179472b7176115aa27d256657f60ad130160fe48",
    "PC4": "4a55be821b0198c60caabf5b88e8c7e5254bb58be7fe4f097c9d2d2ee15c4f87",
    "PC5": "de171fb89c5e6ce32f492fbd52d52ee4fd116ebdaf2738add76b41f63aaf641c",
}

# Present in all 12 projects.
COMMON_FEATURES: List[str] = [
    "BRANCH_COUNT",
    "CYCLOMATIC_COMPLEXITY",
    "DESIGN_COMPLEXITY",
    "ESSENTIAL_COMPLEXITY",
    "HALSTEAD_CONTENT",
    "HALSTEAD_DIFFICULTY",
    "HALSTEAD_EFFORT",
    "HALSTEAD_ERROR_EST",
    "HALSTEAD_LENGTH",
    "HALSTEAD_LEVEL",
    "HALSTEAD_PROG_TIME",
    "HALSTEAD_VOLUME",
    "LOC_CODE_AND_COMMENT",
    "LOC_COMMENTS",
    "LOC_EXECUTABLE",
    "LOC_TOTAL",
    "NUM_OPERANDS",
    "NUM_OPERATORS",
    "NUM_UNIQUE_OPERANDS",
    "NUM_UNIQUE_OPERATORS",
]

# The subset MapVerity can also compute from Python source (see codemetrics.py). Models trained on
# these features can score a team's own transformation code.
PORTABLE_FEATURES: List[str] = [f for f in COMMON_FEATURES if f not in ("DESIGN_COMPLEXITY", "ESSENTIAL_COMPLEXITY")]

LABEL = "Defective"


def default_cache() -> Path:
    return Path(os.environ.get("MAPVERITY_CACHE", Path.home() / ".cache" / "mapverity")) / "nasa-mdp"


def parse_arff(text: str) -> pd.DataFrame:
    """Minimal ARFF reader for numeric attributes and one nominal label."""
    names: List[str] = []
    rows: List[List[str]] = []
    in_data = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        low = s.lower()
        if low.startswith("@attribute"):
            names.append(s.split()[1].strip("'\""))
        elif low.startswith("@data"):
            in_data = True
        elif in_data:
            rows.append([v.strip() for v in s.split(",")])
    df = pd.DataFrame(rows, columns=names)
    # the label is the last attribute; JM1 calls it `label`, the others `Defective`
    if names and LABEL not in df.columns and names[-1].lower() in ("label", "defects", "defective", "bug", "class"):
        df = df.rename(columns={names[-1]: LABEL})
    for c in df.columns:
        if c != LABEL:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if LABEL in df.columns:
        df[LABEL] = df[LABEL].map({"Y": 1, "N": 0, "true": 1, "false": 0, "TRUE": 1, "FALSE": 0}).astype(int)
    return df


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_nasa_mdp(
    names: Optional[Iterable[str]] = None,
    cache_dir: Optional[Union[str, Path]] = None,
    mirror: str = MIRROR,
    verify: bool = True,
) -> Dict[str, Path]:
    """Download (once) and checksum-verify the requested projects; return their local paths."""
    cache = Path(cache_dir) if cache_dir else default_cache()
    cache.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    for name in names or NASA_MDP:
        name = name.upper()
        if name not in NASA_MDP:
            raise KeyError(f"unknown NASA MDP project '{name}' (have {sorted(NASA_MDP)})")
        path = cache / f"{name}.arff"
        if not path.exists() or (verify and _sha256(path.read_bytes()) != NASA_MDP[name]):
            with urllib.request.urlopen(f"{mirror}/{name}.arff", timeout=60) as resp:  # noqa: S310 - fixed https mirror
                data = resp.read()
            if verify and _sha256(data) != NASA_MDP[name]:
                raise ValueError(f"checksum mismatch for {name}: the mirror's file differs from the recorded version")
            path.write_bytes(data)
        out[name] = path
    return out


def load_nasa_mdp(
    names: Optional[Iterable[str]] = None,
    cache_dir: Optional[Union[str, Path]] = None,
    features: Optional[List[str]] = None,
) -> Dict[str, pd.DataFrame]:
    """Return {project: DataFrame(features + Defective)} using the common feature set by default."""
    features = features or COMMON_FEATURES
    frames = {}
    for name, path in fetch_nasa_mdp(names, cache_dir).items():
        df = parse_arff(path.read_text(encoding="utf-8", errors="replace"))
        frames[name] = df[features + [LABEL]].dropna().reset_index(drop=True)
    return frames


def xy(df: pd.DataFrame, features: Optional[List[str]] = None) -> Tuple[pd.DataFrame, pd.Series]:
    features = features or [c for c in df.columns if c != LABEL]
    return df[features], df[LABEL]


def from_bytes(name: str, data: bytes) -> pd.DataFrame:
    """Parse an ARFF already in memory (tests, air-gapped installs)."""
    return parse_arff(io.BytesIO(data).read().decode("utf-8", errors="replace"))
