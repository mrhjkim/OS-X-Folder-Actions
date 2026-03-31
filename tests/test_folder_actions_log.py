"""Tests for FolderActionsLog CLI."""
import json
import os
import sys
import tempfile
import threading
import time
from io import StringIO
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import FolderActionsLog


def _write_entries(log_dir, filename, entries):
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, filename)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


ENTRIES = [
    {"ts": "2026-03-01T10:00:00Z", "file": "invoice_march.pdf", "source": "/Downloads",
     "stage": "ai", "rule": "Tax documents", "confidence": 0.94, "destination": "/Docs/Tax/invoice.pdf", "status": "success"},
    {"ts": "2026-03-15T14:30:00Z", "file": "cat_photo.jpg", "source": "/Downloads",
     "stage": "fallthrough", "rule": None, "destination": None, "status": None},
    {"ts": "2026-03-20T09:00:00Z", "file": "contract_acme.docx", "source": "/Downloads",
     "stage": "ai", "rule": "Work contracts", "confidence": 0.91, "destination": "/Docs/Contracts/contract.docx", "status": "success"},
    {"ts": "2026-03-25T16:00:00Z", "file": "budget_q1.xlsx", "source": "/Downloads",
     "stage": "yaml", "rule": "Weekly report", "confidence": None, "destination": "/Docs/Reports/budget.xlsx", "status": "success"},
]


class TestFolderActionsLog:
    def _run(self, log_dir, args):
        """Run _load_all with test log dir."""
        with patch.object(FolderActionsLog, "LOG_DIR", log_dir):
            return FolderActionsLog._load_all(
                args.get("file_filter"),
                args.get("rule_filter"),
                args.get("since_dt"),
            )

    def test_empty_log_dir_no_crash(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        os.makedirs(log_dir)
        result = self._run(log_dir, {})
        assert result == []

    def test_missing_log_dir_no_crash(self, tmp_path):
        log_dir = str(tmp_path / "nonexistent")
        result = self._run(log_dir, {})
        assert result == []

    def test_loads_all_entries(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        _write_entries(log_dir, "downloads-abc123.jsonl", ENTRIES)
        result = self._run(log_dir, {})
        assert len(result) == len(ENTRIES)

    def test_file_filter(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        _write_entries(log_dir, "downloads-abc123.jsonl", ENTRIES)
        result = self._run(log_dir, {"file_filter": "invoice"})
        assert len(result) == 1
        assert result[0]["file"] == "invoice_march.pdf"

    def test_rule_filter(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        _write_entries(log_dir, "downloads-abc123.jsonl", ENTRIES)
        result = self._run(log_dir, {"rule_filter": "Tax"})
        assert len(result) == 1
        assert result[0]["rule"] == "Tax documents"

    def test_since_filter_excludes_old_entries(self, tmp_path):
        from datetime import datetime, timezone
        log_dir = str(tmp_path / "logs")
        _write_entries(log_dir, "downloads-abc123.jsonl", ENTRIES)
        since = datetime(2026, 3, 15, tzinfo=timezone.utc)
        result = self._run(log_dir, {"since_dt": since})
        # Only entries on or after 2026-03-15
        assert all(e["ts"] >= "2026-03-15" for e in result)
        assert len(result) == 3

    def test_intent_entries_skipped(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        entries = list(ENTRIES) + [
            {"ts": "2026-03-26T00:00:00Z", "file": "tmp.pdf", "stage": "ai",
             "rule": "Tax documents", "status": "intent"}
        ]
        _write_entries(log_dir, "downloads-abc123.jsonl", entries)
        result = self._run(log_dir, {})
        # intent entry should be excluded
        assert all(e.get("status") != "intent" for e in result)

    def test_watch_detects_new_entries(self, tmp_path):
        """Write a new entry after watch starts — verify it is picked up."""
        log_dir = str(tmp_path / "logs")
        _write_entries(log_dir, "downloads-abc123.jsonl", ENTRIES[:1])

        collected = []

        def run_watch():
            with patch.object(FolderActionsLog, "LOG_DIR", log_dir):
                seen = set()
                for entry in FolderActionsLog._load_all(None, None, None):
                    seen.add(FolderActionsLog._entry_key(entry))
                # Poll once
                time.sleep(0.5)
                current = FolderActionsLog._load_all(None, None, None)
                for e in current:
                    key = FolderActionsLog._entry_key(e)
                    if key not in seen:
                        collected.append(e)

        t = threading.Thread(target=run_watch)
        t.start()

        # Write new entry while "watching"
        time.sleep(0.1)
        with open(os.path.join(log_dir, "downloads-abc123.jsonl"), "a") as f:
            f.write(json.dumps(ENTRIES[2]) + "\n")

        t.join(timeout=3)
        assert len(collected) >= 1
        assert collected[0]["file"] == "contract_acme.docx"
