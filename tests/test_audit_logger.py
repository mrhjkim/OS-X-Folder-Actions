"""Tests for AuditLogger."""
import json
import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from AuditLogger import AuditLogger


def _make_logger(tmp_path):
    folder = str(tmp_path / "watched")
    os.makedirs(folder, exist_ok=True)
    log_dir = str(tmp_path / "logs")
    return AuditLogger(folder, log_dir=log_dir), log_dir


class TestAuditLogger:
    def test_write_creates_file_and_directory(self, tmp_path):
        logger, log_dir = _make_logger(tmp_path)
        logger.write({"ts": "2026-01-01T00:00:00Z", "file": "test.pdf", "stage": "yaml"})
        assert os.path.isdir(log_dir)
        assert os.path.isfile(logger.log_path)

    def test_write_produces_valid_jsonl(self, tmp_path):
        logger, _ = _make_logger(tmp_path)
        logger.write({"ts": "2026-01-01T00:00:00Z", "file": "a.pdf", "stage": "yaml"})
        logger.write({"ts": "2026-01-01T00:01:00Z", "file": "b.pdf", "stage": "fallthrough"})
        with open(logger.log_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2
        assert lines[0]["file"] == "a.pdf"
        assert lines[1]["stage"] == "fallthrough"

    def test_write_intent_returns_id(self, tmp_path):
        logger, _ = _make_logger(tmp_path)
        entry_id = logger.write_intent({"ts": "2026-01-01T00:00:00Z", "file": "x.pdf"})
        assert isinstance(entry_id, str)
        assert len(entry_id) > 0
        with open(logger.log_path) as f:
            entry = json.loads(f.readline())
        assert entry["id"] == entry_id
        assert entry["status"] == "intent"

    def test_update_modifies_correct_entry(self, tmp_path):
        logger, _ = _make_logger(tmp_path)
        entry_id = logger.write_intent({"ts": "2026-01-01T00:00:00Z", "file": "invoice.pdf"})
        logger.update(entry_id, status="success", destination="/dest/invoice.pdf")
        with open(logger.log_path) as f:
            entry = json.loads(f.readline())
        assert entry["status"] == "success"
        assert entry["destination"] == "/dest/invoice.pdf"

    def test_update_missing_entry_id_no_exception(self, tmp_path):
        logger, _ = _make_logger(tmp_path)
        logger.write({"ts": "2026-01-01T00:00:00Z", "file": "x.pdf", "id": "other-id"})
        # Should not raise
        logger.update("nonexistent-id", status="success")

    def test_string_fields_capped_at_1024(self, tmp_path):
        logger, _ = _make_logger(tmp_path)
        long_string = "x" * 2000
        logger.write({"ts": "2026-01-01T00:00:00Z", "file": "f.pdf", "reason": long_string})
        with open(logger.log_path) as f:
            entry = json.loads(f.readline())
        assert len(entry["reason"]) == 1024

    def test_concurrent_appends_no_corruption(self, tmp_path):
        """Two threads appending simultaneously — both entries must be present."""
        logger, _ = _make_logger(tmp_path)

        def append_entry(n):
            logger.write({"ts": f"2026-01-01T00:0{n}:00Z", "file": f"file{n}.pdf", "stage": "yaml"})

        t1 = threading.Thread(target=append_entry, args=(1,))
        t2 = threading.Thread(target=append_entry, args=(2,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        with open(logger.log_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2
        files = {e["file"] for e in lines}
        assert "file1.pdf" in files
        assert "file2.pdf" in files
