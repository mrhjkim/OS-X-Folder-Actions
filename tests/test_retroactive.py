"""Tests for retroactive apply feature in FolderActionsDashboard.py.

Covers:
  - get_processed_files()  — audit log lookup
  - scan_folder_for_rule() — folder scan with criteria matching
  - _handle_retroactive()  — HTTP endpoint guards + preview + run logic
"""
import io
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from FolderActionsDashboard import (
    MAX_RETROACTIVE_PREVIEW_FILES,
    MAX_RETROACTIVE_RUN_FILES,
    get_processed_files,
    scan_folder_for_rule,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _write_log(log_path: str, entries: list[dict]):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_request(body: dict | None = None, *, content_length_override: int | None = None):
    """Return a minimal fake handler for _handle_retroactive tests."""
    from FolderActionsDashboard import DashboardHandler

    raw = json.dumps(body or {}).encode("utf-8")
    if content_length_override is not None:
        cl = content_length_override
    else:
        cl = len(raw)

    handler = DashboardHandler.__new__(DashboardHandler)
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(cl)}
    handler._responses = []

    def fake_send_json(data, status=200):
        handler._responses.append((status, data))

    handler._send_json = fake_send_json
    return handler


# ──────────────────────────────────────────────────────────────────────────────
# 1. get_processed_files — returns files found in audit log for matching rule
# ──────────────────────────────────────────────────────────────────────────────

class TestGetProcessedFiles:
    def test_matches_rule(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        # Build the log path AuditLogger would use
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        _write_log(log_path, [
            {"rule": "PDF rule", "status": "success", "item": f"{folder}/invoice.pdf", "ts": "2026-01-01T00:00:00"},
            {"rule": "Other rule", "status": "success", "item": f"{folder}/other.txt", "ts": "2026-01-02T00:00:00"},
        ])

        result = get_processed_files(folder, "PDF rule")
        assert result == {"invoice.pdf": "2026-01-01T00:00:00"}

    def test_no_log_returns_empty(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        result = get_processed_files(folder, "PDF rule")
        assert result == {}

    def test_last_write_wins(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        _write_log(log_path, [
            {"rule": "R", "status": "success", "item": f"{folder}/f.pdf", "ts": "2026-01-01T00:00:00"},
            {"rule": "R", "status": "success", "item": f"{folder}/f.pdf", "ts": "2026-02-01T00:00:00"},
        ])

        result = get_processed_files(folder, "R")
        assert result["f.pdf"] == "2026-02-01T00:00:00"

    def test_intent_status_counts(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        _write_log(log_path, [
            {"rule": "R", "status": "intent", "item": f"{folder}/doc.pdf", "ts": "2026-01-01T00:00:00"},
        ])

        result = get_processed_files(folder, "R")
        assert "doc.pdf" in result

    # regression: .FolderActions.py writes "file" key, not "item"
    def test_file_key_recognized(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        _write_log(log_path, [
            # "file" key — as written by .FolderActions.py
            {"rule": "R", "status": "success", "file": f"{folder}/doc.pdf", "ts": "2026-01-01T00:00:00"},
            # "item" key — legacy format
            {"rule": "R", "status": "success", "item": f"{folder}/old.pdf", "ts": "2026-01-01T00:00:00"},
        ])

        result = get_processed_files(folder, "R")
        assert "doc.pdf" in result, "must recognize 'file' key written by .FolderActions.py"
        assert "old.pdf" in result, "must still recognize legacy 'item' key"

    def test_error_status_excluded(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        _write_log(log_path, [
            {"rule": "R", "status": "error", "item": f"{folder}/bad.pdf", "ts": "2026-01-01T00:00:00"},
        ])

        result = get_processed_files(folder, "R")
        assert result == {}

    # 14. malformed JSON line → line skipped, does not crash
    def test_skips_malformed_lines(self, tmp_path):
        folder = str(tmp_path / "watched")
        os.makedirs(folder)
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder).log_path

        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w") as f:
            f.write('{"rule":"R","status":"success","item":"' + folder + '/ok.pdf","ts":"2026-01-01T00:00:00"}\n')
            f.write("NOT VALID JSON\n")
            f.write("{incomplete\n")

        result = get_processed_files(folder, "R")
        assert result == {"ok.pdf": "2026-01-01T00:00:00"}


# ──────────────────────────────────────────────────────────────────────────────
# 2. scan_folder_for_rule
# ──────────────────────────────────────────────────────────────────────────────

class TestScanFolderForRule:
    # 7. criteria matching: file with wrong extension not included
    def test_excludes_non_matching_files(self, tmp_path):
        folder = str(tmp_path)
        (tmp_path / "report.pdf").touch()
        (tmp_path / "report.txt").touch()

        criteria = [{"FileExtension": "pdf"}]
        result = scan_folder_for_rule(folder, criteria)
        names = [os.path.basename(p) for p in result]
        assert "report.pdf" in names
        assert "report.txt" not in names

    # 9. CRITICAL regression: FileNameContains uses basename, not full path
    def test_uses_basename_for_criteria(self, tmp_path):
        """Folder name must NOT affect FileNameContains evaluation."""
        # Create folder whose name would NOT match "invoice"
        folder = str(tmp_path / "no_match_dir")
        os.makedirs(folder)
        # File named "invoice.pdf" — should match despite containing folder name
        open(os.path.join(folder, "invoice.pdf"), "w").close()
        # File named "other.pdf" — should NOT match
        open(os.path.join(folder, "other.pdf"), "w").close()

        criteria = [{"FileNameContains": "invoice"}]
        result = scan_folder_for_rule(folder, criteria)
        names = [os.path.basename(p) for p in result]
        assert "invoice.pdf" in names
        assert "other.pdf" not in names

    def test_directory_name_does_not_affect_filename_contains(self, tmp_path):
        """If the folder itself contains the search term, files without it must not match."""
        # Folder name contains "invoice" — child files WITHOUT "invoice" must NOT match
        folder = str(tmp_path / "invoice_dir")
        os.makedirs(folder)
        open(os.path.join(folder, "random.pdf"), "w").close()

        criteria = [{"FileNameContains": "invoice"}]
        result = scan_folder_for_rule(folder, criteria)
        names = [os.path.basename(p) for p in result]
        assert "random.pdf" not in names

    # 15. folder not found → returns []
    def test_folder_not_found_returns_empty(self, tmp_path):
        missing = str(tmp_path / "does_not_exist")
        result = scan_folder_for_rule(missing, [{"FileExtension": "pdf"}])
        assert result == []

    def test_no_criteria_matches_all_files(self, tmp_path):
        (tmp_path / "a.pdf").touch()
        (tmp_path / "b.txt").touch()
        result = scan_folder_for_rule(str(tmp_path), [])
        assert len(result) == 2


# ──────────────────────────────────────────────────────────────────────────────
# 3. _handle_retroactive — HTTP endpoint
# ──────────────────────────────────────────────────────────────────────────────

class TestHandleRetroactive:
    def _call(self, body, *, content_length_override=None, sources=None, apply_rule=None):
        """Helper: call _handle_retroactive with mocked data layer."""
        handler = _make_request(body, content_length_override=content_length_override)

        default_sources = sources if sources is not None else []
        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=default_sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module") as mock_mod:
            if apply_rule is not None:
                mock_fa = MagicMock()
                mock_fa.apply_rule_by_yaml_config = apply_rule
                mock_fa.match_criteria = MagicMock(return_value=True)
                mock_mod.return_value = mock_fa
            else:
                mock_mod.return_value = None
            handler._handle_retroactive()

        return handler._responses

    # 10. body size limit: POST body > MAX_BODY_BYTES → 413
    def test_body_too_large(self):
        responses = self._call({}, content_length_override=2 * 1024 * 1024)
        assert responses[0][0] == 413

    # invalid action → 400
    def test_invalid_action(self):
        responses = self._call({"source_index": 0, "rule_index": 0, "action": "invalid"})
        assert responses[0][0] == 400

    # 13. rule_index out of bounds → 400
    def test_invalid_rule_index(self):
        sources = [{"folder": "/tmp", "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": "/tmp/.FolderActions.yaml"}]
        responses = self._call({"source_index": 0, "rule_index": 99, "action": "preview"}, sources=sources)
        assert responses[0][0] == 400

    def test_invalid_source_index(self):
        responses = self._call({"source_index": 99, "rule_index": 0, "action": "preview"}, sources=[])
        assert responses[0][0] == 400

    # bool subclass of int: True should not be treated as source_index=1
    def test_bool_source_index_rejected(self):
        responses = self._call({"source_index": True, "rule_index": 0, "action": "preview"}, sources=[])
        assert responses[0][0] == 400

    # 3. preview returns correct processed/unprocessed split
    def test_preview_returns_correct_status(self, tmp_path):
        folder = str(tmp_path)
        f_processed = str(tmp_path / "processed.pdf")
        f_unprocessed = str(tmp_path / "new.pdf")
        open(f_processed, "w").close()
        open(f_unprocessed, "w").close()

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "PDF rule", "mode": "simple", "criteria": [{"type": "ext", "value": "pdf"}],
             "groups": [], "dest": "/dest", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "preview"})
        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[f_processed, f_unprocessed]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={"processed.pdf": "2026-01-01T00:00:00"}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=None):
            handler._handle_retroactive()

        status, data = handler._responses[0]
        assert status == 200
        by_name = {f["name"]: f["status"] for f in data["files"]}
        assert by_name["processed.pdf"] == "processed"
        assert by_name["new.pdf"] == "unprocessed"
        assert data["unprocessed"] == 1  # aggregate count must match

    # 4. run skips files already in audit log
    def test_run_skips_processed_files(self, tmp_path):
        folder = str(tmp_path)
        f = str(tmp_path / "already.pdf")
        open(f, "w").close()

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        mock_apply = MagicMock()
        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})
        mock_fa = MagicMock()
        mock_fa.apply_rule_by_yaml_config = mock_apply

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[f]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={"already.pdf": "2026-01-01T00:00:00"}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        mock_apply.assert_not_called()
        status, data = handler._responses[0]
        assert data["files"][0]["status"] == "processed"

    # 5. run skips files that no longer exist on disk
    def test_run_skips_missing_files(self, tmp_path):
        folder = str(tmp_path)
        missing = str(tmp_path / "gone.pdf")  # never created on disk

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        mock_apply = MagicMock()
        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})
        mock_fa = MagicMock()
        mock_fa.apply_rule_by_yaml_config = mock_apply

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[missing]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        mock_apply.assert_not_called()
        status, data = handler._responses[0]
        assert data["files"][0]["status"] == "skipped"

    # 6. run executes apply_rule_by_yaml_config for unprocessed files
    def test_run_executes_for_unprocessed(self, tmp_path):
        folder = str(tmp_path)
        f = str(tmp_path / "new.pdf")
        open(f, "w").close()

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "PDF rule", "mode": "simple",
             "criteria": [{"type": "ext", "value": "pdf"}],
             "groups": [], "dest": "/dest", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        mock_apply = MagicMock()
        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})
        mock_fa = MagicMock()
        mock_fa.apply_rule_by_yaml_config = mock_apply

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[f]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        mock_apply.assert_called_once()
        call_args = mock_apply.call_args
        # item must be filename-only, not full path
        assert call_args[0][0] == folder
        assert call_args[0][1] == "new.pdf"

        status, data = handler._responses[0]
        assert data["files"][0]["status"] == "run"

    # missing action key → 400 (same as invalid action)
    def test_missing_action_key(self):
        responses = self._call({"source_index": 0, "rule_index": 0})  # no "action" key
        assert responses[0][0] == 400

    # boundary: exactly 100 files for preview → 200 (not 400)
    def test_preview_exactly_at_limit(self, tmp_path):
        folder = str(tmp_path)
        exactly = [str(tmp_path / f"f{i}.pdf") for i in range(MAX_RETROACTIVE_PREVIEW_FILES)]

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "preview"})

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=exactly), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=None):
            handler._handle_retroactive()

        assert handler._responses[0][0] == 200

    # boundary: exactly 50 files for run → 200 (not 400)
    def test_run_exactly_at_limit(self, tmp_path):
        folder = str(tmp_path)
        exactly = [str(tmp_path / f"f{i}.pdf") for i in range(MAX_RETROACTIVE_RUN_FILES)]

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})
        mock_fa = MagicMock()
        mock_fa.apply_rule_by_yaml_config = MagicMock()

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=exactly), \
             patch("FolderActionsDashboard.get_processed_files", return_value={"f{}.pdf".format(i): "ts" for i in range(MAX_RETROACTIVE_RUN_FILES)}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        assert handler._responses[0][0] == 200

    # 11. file count limit: preview with > 100 matching files → 400
    def test_preview_too_many_files(self, tmp_path):
        folder = str(tmp_path)
        many_files = [str(tmp_path / f"f{i}.pdf") for i in range(MAX_RETROACTIVE_PREVIEW_FILES + 1)]

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "preview"})

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=many_files), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=None):
            handler._handle_retroactive()

        assert handler._responses[0][0] == 400

    # error path: apply_rule raises an exception → status "error"
    def test_run_apply_rule_exception_gives_error_status(self, tmp_path):
        folder = str(tmp_path)
        f = str(tmp_path / "bad.pdf")
        open(f, "w").close()

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})
        mock_fa = MagicMock()
        mock_fa.apply_rule_by_yaml_config = explode

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[f]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        status, data = handler._responses[0]
        assert status == 200
        assert data["files"][0]["status"] == "error"
        # exception message must NOT be leaked in the response
        assert data["files"][0]["last_processed"] == ""

    # error path: fa_mod missing apply_rule_by_yaml_config → apply_rule is None → status "error"
    def test_run_missing_apply_rule_gives_error_status(self, tmp_path):
        folder = str(tmp_path)
        f = str(tmp_path / "file.pdf")
        open(f, "w").close()

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        # fa_mod loaded but lacks apply_rule_by_yaml_config
        mock_fa = MagicMock(spec=[])  # spec=[] → no attributes → getattr returns None via hasattr

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=[f]), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=mock_fa):
            handler._handle_retroactive()

        status, data = handler._responses[0]
        assert status == 200
        assert data["files"][0]["status"] == "error"

    # 12. file count limit: run with > 50 matching files → 400
    def test_run_too_many_files(self, tmp_path):
        folder = str(tmp_path)
        many_files = [str(tmp_path / f"f{i}.pdf") for i in range(MAX_RETROACTIVE_RUN_FILES + 1)]

        sources = [{"folder": folder, "rules": [
            {"id": "r0", "title": "R", "mode": "simple", "criteria": [], "groups": [], "dest": "", "actions": [], "modified": False, "isNew": False}
        ], "yamlPath": os.path.join(folder, ".FolderActions.yaml")}]

        handler = _make_request({"source_index": 0, "rule_index": 0, "action": "run"})

        with patch("FolderActionsDashboard.load_logs", return_value=[]), \
             patch("FolderActionsDashboard.find_sources", return_value=sources), \
             patch("FolderActionsDashboard.scan_folder_for_rule", return_value=many_files), \
             patch("FolderActionsDashboard.get_processed_files", return_value={}), \
             patch("FolderActionsDashboard._load_folder_actions_module", return_value=None):
            handler._handle_retroactive()

        assert handler._responses[0][0] == 400
