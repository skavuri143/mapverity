"""Read mainframe files described by a COBOL copybook.

Supported: PIC X/A/9 with S and V, repeat counts like X(10)/9(7)V99, DISPLAY (incl. zoned sign
overpunch), COMP-3 / PACKED-DECIMAL, COMP / COMP-4 / COMP-5 / BINARY, OCCURS n TIMES on items
and groups, FILLER, REDEFINES (the redefining item is skipped), level 88 condition names
(ignored), EBCDIC (cp037 by default) and ASCII, fixed-length or line-sequential records.

Not supported: OCCURS DEPENDING ON, COMP-1/COMP-2 floating point, SIGN SEPARATE, P scaling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import pandas as pd

_PIC_RE = re.compile(r"(?:PIC|PICTURE)\s+(?:IS\s+)?(\S+)", re.I)
_OCCURS_RE = re.compile(r"OCCURS\s+(\d+)(?:\s+TIMES)?", re.I)
_REDEFINES_RE = re.compile(r"REDEFINES\s+([\w-]+)", re.I)


@dataclass
class Field:
    level: int
    name: str
    pic: Optional[str] = None
    usage: str = "DISPLAY"
    occurs: int = 1
    redefines: Optional[str] = None
    children: List["Field"] = field(default_factory=list)
    # derived for elementary items
    kind: str = "group"  # group | alnum | numeric
    digits: int = 0
    scale: int = 0
    signed: bool = False

    @property
    def is_group(self) -> bool:
        return self.pic is None

    @property
    def column(self) -> str:
        return self.name.lower().replace("-", "_")

    def size(self) -> int:
        """Bytes for ONE occurrence."""
        if self.redefines:
            return 0
        if self.is_group:
            return sum(c.size() * c.occurs for c in self.children)
        if self.kind == "alnum":
            return self.digits
        if self.usage == "COMP-3":
            return self.digits // 2 + 1
        if self.usage == "BINARY":
            return 2 if self.digits <= 4 else 4 if self.digits <= 9 else 8
        return self.digits


def _expand_pic(pic: str) -> str:
    return re.sub(r"(.)\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), pic.upper())


def _classify(f: Field) -> None:
    pic = _expand_pic(f.pic or "")
    f.signed = pic.startswith("S")
    pic = pic.lstrip("S")
    if set(pic) <= set("9V"):
        f.kind = "numeric"
        int_part, _, frac = pic.partition("V")
        f.digits = len(int_part) + len(frac)
        f.scale = len(frac)
    elif set(pic) <= set("XA9"):
        f.kind = "alnum"
        f.digits = len(pic)
    else:
        raise ValueError(f"unsupported PIC clause '{f.pic}' for {f.name}")


def _statements(text: str) -> Iterable[str]:
    cleaned = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if (
            len(line) > 6
            and (line[:6].strip() == "" or line[:6].strip().isdigit())
            and line[6:7] in (" ", "*", "-", "/")
        ):
            if line[6:7] in ("*", "/"):
                continue
            line = line[7:72]  # fixed-format: drop sequence area, indicator, and columns 73+
        if line.strip().startswith("*"):
            continue
        cleaned.append(line)
    for stmt in " ".join(cleaned).split("."):
        stmt = " ".join(stmt.split())
        if stmt:
            yield stmt


def parse_copybook(text: str) -> Field:
    """Parse copybook text into a tree rooted at a synthetic level-0 group."""
    root = Field(level=0, name="ROOT")
    stack = [root]
    for stmt in _statements(text):
        tokens = stmt.split()
        if not tokens[0].isdigit():
            continue
        level = int(tokens[0])
        if level in (66, 77, 88):
            continue
        name = tokens[1] if len(tokens) > 1 else "FILLER"
        f = Field(level=level, name=name)
        m = _PIC_RE.search(stmt)
        if m:
            f.pic = m.group(1)
            _classify(f)
        up = stmt.upper()
        if re.search(r"\b(COMP-3|COMPUTATIONAL-3|PACKED-DECIMAL)\b", up):
            f.usage = "COMP-3"
        elif re.search(r"\b(COMP|COMP-4|COMP-5|COMPUTATIONAL|BINARY)\b", up):
            f.usage = "BINARY"
        elif re.search(r"\b(COMP-1|COMP-2)\b", up):
            raise ValueError(f"floating point usage not supported ({name})")
        if "DEPENDING" in up:
            raise ValueError(f"OCCURS DEPENDING ON is not supported ({name})")
        m = _OCCURS_RE.search(stmt)
        if m:
            f.occurs = int(m.group(1))
        m = _REDEFINES_RE.search(stmt)
        if m:
            f.redefines = m.group(1)
        while stack[-1].level >= level:
            stack.pop()
        stack[-1].children.append(f)
        stack.append(f)
    return root


@dataclass
class Layout:
    """Flat list of (column, field, offset) for one record."""

    root: Field
    columns: List[tuple] = field(default_factory=list)
    record_length: int = 0

    @classmethod
    def from_copybook(cls, text: str) -> "Layout":
        root = parse_copybook(text)
        lay = cls(root)
        lay.record_length = lay._walk(root, 0, "")
        return lay

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "Layout":
        return cls.from_copybook(Path(path).read_text(encoding="utf-8"))

    def _walk(self, f: Field, offset: int, suffix: str) -> int:
        if f.redefines:
            return offset
        for i in range(f.occurs):
            sfx = suffix + (f"_{i + 1}" if f.occurs > 1 else "")
            if f.is_group:
                for c in f.children:
                    offset = self._walk(c, offset, sfx)
            else:
                if f.name.upper() != "FILLER":
                    self.columns.append((f.column + sfx, f, offset))
                offset += f.size()
        return offset


# --------------------------------------------------------------------------- decoding


_ASCII_OVERPUNCH = {
    **{c: (str(i), 1) for i, c in enumerate("{ABCDEFGHI")},
    **{c: (str(i), -1) for i, c in enumerate("}JKLMNOPQR")},
}


def _decode_zoned(raw: bytes, f: Field, encoding: str) -> Optional[Decimal]:
    if encoding.lower() in ("cp037", "cp500", "cp1047", "ebcdic"):
        if not raw.strip(b"\x40\x00"):
            return None
        sign = -1 if (raw[-1] >> 4) == 0xD else 1
        digits = "".join(str(b & 0x0F) for b in raw)
    else:
        text = raw.decode("latin-1")
        if not text.strip():
            return None
        sign = 1
        last = text[-1]
        if last in _ASCII_OVERPUNCH:
            d, sign = _ASCII_OVERPUNCH[last]
            text = text[:-1] + d
        if text.strip().startswith("-"):
            sign, text = -1, text.replace("-", "")
        digits = text.strip().replace("+", "").zfill(f.digits)
        if not digits.isdigit():
            raise ValueError(f"invalid DISPLAY numeric {raw!r} for {f.name}")
    return Decimal(sign * int(digits)).scaleb(-f.scale)


def _decode_packed(raw: bytes, f: Field) -> Decimal:
    nibbles = []
    for b in raw:
        nibbles += [b >> 4, b & 0x0F]
    sign_nibble = nibbles.pop()
    if any(n > 9 for n in nibbles):
        raise ValueError(f"invalid COMP-3 data {raw.hex()} for {f.name}")
    value = int("".join(map(str, nibbles)) or "0")
    if sign_nibble == 0xD:
        value = -value
    return Decimal(value).scaleb(-f.scale)


def decode_field(raw: bytes, f: Field, encoding: str) -> Any:
    if f.kind == "alnum":
        return raw.decode(encoding).rstrip()
    v: Optional[Decimal]
    if f.usage == "COMP-3":
        v = _decode_packed(raw, f)
    elif f.usage == "BINARY":
        v = Decimal(int.from_bytes(raw, "big", signed=f.signed)).scaleb(-f.scale)
    else:
        v = _decode_zoned(raw, f, encoding)
    if v is None:
        return None
    return int(v) if f.scale == 0 else float(v)


def _records(data: bytes, length: int, line_sequential: bool) -> Iterable[bytes]:
    if line_sequential:
        for line in data.splitlines():
            if line.strip():
                yield line.ljust(length, b" ")
        return
    if len(data) % length:
        raise ValueError(f"file size {len(data)} is not a multiple of record length {length}")
    for i in range(0, len(data), length):
        yield data[i : i + length]


def read_cobol(
    source: Union[str, Path, bytes],
    copybook: Union[str, Path, Layout],
    encoding: str = "cp037",
    line_sequential: Optional[bool] = None,
) -> pd.DataFrame:
    """Decode a fixed-record file into a DataFrame with one column per elementary item."""
    layout = copybook if isinstance(copybook, Layout) else Layout.from_file(copybook)
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if line_sequential is None:
        line_sequential = encoding.lower() not in ("cp037", "cp500", "cp1047") and b"\n" in data
    rows = []
    for rec in _records(data, layout.record_length, line_sequential):
        rows.append({col: decode_field(rec[off : off + f.size()], f, encoding) for col, f, off in layout.columns})
    return pd.DataFrame(rows, columns=[c for c, _, _ in layout.columns])


# --------------------------------------------------------------------------- encoding (test-data generation)


def _encode_zoned(value: Optional[Decimal], f: Field, encoding: str) -> bytes:
    if value is None:
        return (" " * f.digits).encode(encoding)
    n = int((Decimal(str(value)) * (10**f.scale)).to_integral_value())
    digits = str(abs(n)).zfill(f.digits)[-f.digits :]
    if encoding.lower() in ("cp037", "cp500", "cp1047"):
        out = bytearray(0xF0 | int(d) for d in digits)
        if f.signed:
            out[-1] = ((0xD if n < 0 else 0xC) << 4) | int(digits[-1])
        return bytes(out)
    if f.signed and n < 0:
        digits = digits[:-1] + "}JKLMNOPQR"[int(digits[-1])]
    return digits.encode(encoding)


def _encode_packed(value, f: Field) -> bytes:
    n = int((Decimal(str(value or 0)) * (10**f.scale)).to_integral_value())
    nib = [int(d) for d in str(abs(n)).zfill(f.size() * 2 - 1)[-(f.size() * 2 - 1) :]]
    nib.append(0xD if n < 0 else (0xC if f.signed else 0xF))
    return bytes((nib[i] << 4) | nib[i + 1] for i in range(0, len(nib), 2))


def encode_record(values: Dict[str, Any], layout: Layout, encoding: str = "cp037") -> bytes:
    buf = bytearray(
        b" " * layout.record_length
        if encoding.lower() not in ("cp037", "cp500", "cp1047")
        else b"\x40" * layout.record_length
    )
    for col, f, off in layout.columns:
        v = values.get(col)
        if f.kind == "alnum":
            raw = ("" if v is None else str(v))[: f.size()].ljust(f.size()).encode(encoding)
        elif f.usage == "COMP-3":
            raw = _encode_packed(v, f)
        elif f.usage == "BINARY":
            n = int((Decimal(str(v or 0)) * (10**f.scale)).to_integral_value())
            raw = n.to_bytes(f.size(), "big", signed=f.signed)
        else:
            raw = _encode_zoned(None if v is None else Decimal(str(v)), f, encoding)
        buf[off : off + f.size()] = raw
    return bytes(buf)


def write_cobol(
    rows: Iterable[Dict[str, Any]],
    layout: Layout,
    path: Union[str, Path],
    encoding: str = "cp037",
    line_sequential: bool = False,
) -> None:
    sep = b"\n" if line_sequential else b""
    with open(path, "wb") as fh:
        for r in rows:
            fh.write(encode_record(r, layout, encoding) + sep)
