"""ContentExtractor .xls path (old binary Excel via xlrd).

xlrd is mocked with a fake workbook so the test needs no binary fixture and no
real xlrd. Covers: dispatch (.xls → _extract_xls), cell join, sheet-1-only,
MAX_CHARS truncation, and the extract() broad try/except swallowing xlrd errors.
"""
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ContentExtractor


class _FakeSheet:
    def __init__(self, rows):
        self._rows = rows
        self.nrows = len(rows)

    def row_values(self, r):
        return self._rows[r]


class _FakeBook:
    def __init__(self, sheets):
        self._sheets = sheets

    def sheet_by_index(self, i):
        return self._sheets[i]


def _install_fake_xlrd(monkeypatch, sheets, open_error=None):
    fake = types.ModuleType("xlrd")

    def open_workbook(path):
        if open_error:
            raise open_error
        return _FakeBook(sheets)

    fake.open_workbook = open_workbook
    monkeypatch.setitem(sys.modules, "xlrd", fake)


def _xls_tmp():
    f = tempfile.NamedTemporaryFile("wb", suffix=".xls", delete=False)
    f.write(b"\xd0\xcf\x11\xe0")   # OLE2 magic; content is irrelevant, xlrd is mocked
    f.close()
    return f.name


def test_xls_dispatched_and_cells_joined(monkeypatch):
    _install_fake_xlrd(monkeypatch, [_FakeSheet([
        ["SKT 지능망", "개발 계획", None],
        ["요구사항 분석", "", "설계"],
    ])])
    path = _xls_tmp()
    try:
        out = ContentExtractor.extract(path)
    finally:
        os.unlink(path)
    assert "SKT 지능망" in out and "개발 계획" in out and "설계" in out
    assert "None" not in out          # None cells skipped
    assert "  " not in out.strip()    # no double space from the empty-string cell


def test_xls_reads_first_sheet_only(monkeypatch):
    _install_fake_xlrd(monkeypatch, [
        _FakeSheet([["sheet-one"]]),
        _FakeSheet([["sheet-two"]]),
    ])
    path = _xls_tmp()
    try:
        out = ContentExtractor.extract(path)
    finally:
        os.unlink(path)
    assert "sheet-one" in out
    assert "sheet-two" not in out


def test_xls_truncated_to_max_chars(monkeypatch):
    big = [["가" * 1000] for _ in range(20)]   # 20k chars of cells
    _install_fake_xlrd(monkeypatch, [_FakeSheet(big)])
    path = _xls_tmp()
    try:
        out = ContentExtractor.extract(path)
    finally:
        os.unlink(path)
    assert len(out) == ContentExtractor.MAX_CHARS


def test_xls_extraction_failure_returns_empty(monkeypatch):
    _install_fake_xlrd(monkeypatch, [], open_error=RuntimeError("corrupt xls"))
    path = _xls_tmp()
    try:
        out = ContentExtractor.extract(path)   # broad try/except → "" not a crash
    finally:
        os.unlink(path)
    assert out == ""


# --- OOXML Excel (.xlsx / .xlsm / .xltx / .xltm) via openpyxl ---

class _FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, values_only=True):
        return iter(self._rows)


class _FakeWB:
    def __init__(self, rows):
        self.active = _FakeWS(rows)


def _install_fake_openpyxl(monkeypatch, rows, load_error=None):
    fake = types.ModuleType("openpyxl")

    def load_workbook(path, read_only=True, data_only=True, **kw):
        if load_error:
            raise load_error
        return _FakeWB(rows)

    fake.load_workbook = load_workbook
    monkeypatch.setitem(sys.modules, "openpyxl", fake)


def _tmp(suffix):
    f = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
    f.write(b"PK\x03\x04")   # zip magic; irrelevant, openpyxl is mocked
    f.close()
    return f.name


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xltx", ".xltm", ".XLSM"])
def test_ooxml_extensions_dispatch_to_openpyxl(monkeypatch, suffix):
    _install_fake_openpyxl(monkeypatch, [
        ("Staging 기능 개발", None, "개발3팀"),
        ("요구사항", "", "설계"),
    ])
    path = _tmp(suffix)
    try:
        out = ContentExtractor.extract(path)
    finally:
        os.unlink(path)
    assert "Staging 기능 개발" in out and "설계" in out
    assert "None" not in out          # None cells skipped


def test_xlsm_extraction_failure_returns_empty(monkeypatch):
    _install_fake_openpyxl(monkeypatch, [], load_error=RuntimeError("bad zip"))
    path = _tmp(".xlsm")
    try:
        out = ContentExtractor.extract(path)   # broad try/except → "" not a crash
    finally:
        os.unlink(path)
    assert out == ""
