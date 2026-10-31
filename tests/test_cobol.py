import pytest

from mapverity.validation.cobol import Layout, encode_record, parse_copybook, read_cobol, write_cobol

COPYBOOK = """
      * comment line
       01  REC.
           05  ID              PIC X(4).
           05  AMOUNT          PIC S9(5)V99 COMP-3.
           05  QTY             PIC S9(3).
           05  BIN-CT          PIC 9(4) COMP.
           05  CODES OCCURS 2 TIMES.
               10  CODE        PIC X(2).
           05  ALT REDEFINES CODES PIC X(4).
           05  STATUS          PIC X.
               88  ACTIVE      VALUE 'A'.
           05  FILLER          PIC X(3).
"""


def test_layout_offsets_and_length():
    lay = Layout.from_copybook(COPYBOOK)
    cols = {c: (f.size(), off) for c, f, off in lay.columns}
    assert cols["id"] == (4, 0)
    assert cols["amount"] == (4, 4)  # 7 digits packed -> 4 bytes
    assert cols["qty"] == (3, 8)
    assert cols["bin_ct"] == (2, 11)  # 4 digits binary -> halfword
    assert cols["code_1"] == (2, 13) and cols["code_2"] == (2, 15)
    assert "alt" not in cols  # REDEFINES skipped
    assert cols["status"] == (1, 17)
    assert lay.record_length == 21  # incl. FILLER


@pytest.mark.parametrize("encoding", ["cp037", "ascii"])
def test_round_trip(tmp_path, encoding):
    lay = Layout.from_copybook(COPYBOOK)
    rows = [
        {"id": "A001", "amount": -1234.56, "qty": -7, "bin_ct": 300, "code_1": "XY", "code_2": "Z", "status": "A"},
        {"id": "B2", "amount": 0.01, "qty": 42, "bin_ct": 0, "code_1": "", "code_2": "QQ", "status": "T"},
    ]
    path = tmp_path / "f.dat"
    write_cobol(rows, lay, path, encoding=encoding)
    df = read_cobol(path, lay, encoding=encoding, line_sequential=False)
    assert df["id"].tolist() == ["A001", "B2"]
    assert df["amount"].tolist() == [-1234.56, 0.01]
    assert df["qty"].tolist() == [-7, 42]
    assert df["bin_ct"].tolist() == [300, 0]
    assert df["code_2"].tolist() == ["Z", "QQ"]


def test_comp3_known_bytes():
    lay = Layout.from_copybook("01 R. 05 A PIC S9(3)V99 COMP-3.")
    raw = encode_record({"a": 123.45}, lay)
    assert raw == bytes.fromhex("12345C")
    raw = encode_record({"a": -0.5}, lay)
    assert raw == bytes.fromhex("00050D")


def test_ebcdic_zoned_sign():
    lay = Layout.from_copybook("01 R. 05 N PIC S9(3).")
    assert encode_record({"n": -12}, lay) == bytes([0xF0, 0xF1, 0xD2])
    df = read_cobol(bytes([0xF0, 0xF1, 0xD2]), lay)
    assert df["n"].iloc[0] == -12


def test_unsupported_depending_on():
    with pytest.raises(ValueError):
        parse_copybook("01 R. 05 N PIC 9. 05 X OCCURS 1 TO 5 DEPENDING ON N PIC X.")


def test_bad_record_length():
    lay = Layout.from_copybook("01 R. 05 A PIC X(3).")
    with pytest.raises(ValueError):
        read_cobol(b"\x40" * 4, lay)
