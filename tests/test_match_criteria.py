"""Tests for match_criteria() and apply_rule_by_yaml_config()."""
import importlib.util
import os
import shutil
import sys
import tempfile
import textwrap
import unicodedata
from unittest.mock import patch

import pytest

# .FolderActions.py has a leading dot — load it via importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_spec = importlib.util.spec_from_file_location(
    "FolderActions",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".FolderActions.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

match_criteria = _mod.match_criteria
apply_rule_by_yaml_config = _mod.apply_rule_by_yaml_config


# ------------------------------------------------------------------
# match_criteria()
# ------------------------------------------------------------------

class TestMatchCriteria:
    def test_file_extension_match(self):
        assert match_criteria("report.xlsx", {"FileExtension": "xlsx"}) is True

    def test_file_extension_no_match(self):
        assert match_criteria("report.pdf", {"FileExtension": "xlsx"}) is False

    def test_file_name_contains_match(self):
        assert match_criteria("주간업무_2026.xlsx", {"FileNameContains": "주간업무"}) is True

    def test_file_name_contains_no_match(self):
        assert match_criteria("월간업무.xlsx", {"FileNameContains": "주간업무"}) is False

    def test_all_criteria_both_match(self):
        criterion = {"AllCriteria": [
            {"FileExtension": "xlsx"},
            {"FileNameContains": "주간업무"},
        ]}
        assert match_criteria("주간업무_Q1.xlsx", criterion) is True

    def test_all_criteria_partial_miss(self):
        criterion = {"AllCriteria": [
            {"FileExtension": "xlsx"},
            {"FileNameContains": "주간업무"},
        ]}
        assert match_criteria("월간업무.xlsx", criterion) is False

    def test_any_criteria_one_matches(self):
        criterion = {"AnyCriteria": [
            {"FileNameContains": "invoice"},
            {"FileNameContains": "receipt"},
        ]}
        assert match_criteria("invoice_march.pdf", criterion) is True

    def test_any_criteria_none_match(self):
        criterion = {"AnyCriteria": [
            {"FileNameContains": "invoice"},
            {"FileNameContains": "receipt"},
        ]}
        assert match_criteria("photo.jpg", criterion) is False

    def test_nested_all_inside_any(self):
        criterion = {"AnyCriteria": [
            {"AllCriteria": [{"FileNameContains": "aaa"}, {"FileNameContains": "bbb"}]},
            {"AllCriteria": [{"FileNameContains": "ccc"}, {"FileNameContains": "ddd"}]},
        ]}
        assert match_criteria("aaa_bbb_file.txt", criterion) is True
        assert match_criteria("ccc_ddd_file.txt", criterion) is True
        assert match_criteria("aaa_ccc_file.txt", criterion) is False

    def test_nfc_normalization(self):
        # NFD decomposed filename should match NFC criterion
        nfd_name = unicodedata.normalize("NFD", "주간업무.xlsx")
        nfc_name = unicodedata.normalize("NFC", nfd_name)
        assert match_criteria(nfc_name, {"FileNameContains": "주간업무"}) is True

    def test_unknown_key_fail_closed(self):
        """Unknown criterion type must return False (fail-closed), not True."""
        assert match_criteria("any_file.txt", {"FilenameContains": "any"}) is False
        assert match_criteria("any_file.txt", {"UnknownKey": "value"}) is False


# ------------------------------------------------------------------
# apply_rule_by_yaml_config()
# ------------------------------------------------------------------

class TestApplyRuleByYamlConfig:
    def _make_folder(self, yaml_content, filenames=None):
        """Create a temp folder with a YAML config and optionally create files."""
        folder = tempfile.mkdtemp()
        yaml_path = os.path.join(folder, ".FolderActions.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(yaml_content))
        if filenames:
            for fn in filenames:
                open(os.path.join(folder, fn), "w").close()
        return folder

    def teardown_method(self):
        # Cleanup temp dirs created per test — handled per-test below
        pass

    def test_no_match_returns_false_tuple(self):
        folder = self._make_folder("""
            Rules:
              - Title: "PDFs"
                Criteria:
                  - FileExtension: pdf
                Actions:
                  - MoveToFolder: /tmp/pdfs/
        """, filenames=["photo.jpg"])
        try:
            matched, title, dest, err, results = apply_rule_by_yaml_config(folder, "photo.jpg")
            assert matched is False
            assert title is None
            assert dest is None
            assert err is None
            assert results == []
        finally:
            shutil.rmtree(folder)

    def test_move_to_folder_match(self, tmp_path):
        target = tmp_path / "output"
        folder = self._make_folder(f"""
            Rules:
              - Title: "XLS files"
                Criteria:
                  - FileExtension: xlsx
                Actions:
                  - MoveToFolder: "{target}"
        """, filenames=["report.xlsx"])
        try:
            matched, title, dest, err, results = apply_rule_by_yaml_config(folder, "report.xlsx")
            assert matched is True
            assert title == "XLS files"
            assert err is None
            assert results[0]["action"] == "MoveToFolder"
            assert os.path.exists(os.path.join(str(target), "report.xlsx"))
        finally:
            shutil.rmtree(folder)

    def test_rule_no_title_defaults_to_unnamed(self, tmp_path):
        target = tmp_path / "out"
        folder = self._make_folder(f"""
            Rules:
              - Criteria:
                  - FileExtension: pdf
                Actions:
                  - MoveToFolder: "{target}"
        """, filenames=["doc.pdf"])
        try:
            matched, title, dest, err, _ = apply_rule_by_yaml_config(folder, "doc.pdf")
            assert matched is True
            assert title == "(unnamed)"
        finally:
            shutil.rmtree(folder)

    def test_yaml_missing_rules_key(self):
        folder = self._make_folder("""
            Audit:
              Enabled: false
        """, filenames=["file.txt"])
        try:
            matched, title, dest, err, _ = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is False
        finally:
            shutil.rmtree(folder)

    def test_move_oserror_returns_action_error(self, tmp_path, monkeypatch):
        target = tmp_path / "out"
        folder = self._make_folder(f"""
            Rules:
              - Title: "test"
                Criteria:
                  - FileExtension: txt
                Actions:
                  - MoveToFolder: "{target}"
        """, filenames=["file.txt"])
        try:
            import shutil as _shutil
            monkeypatch.setattr(_shutil, "move", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
            matched, title, dest, err, results = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is True
            assert "disk full" in err
            assert results[0]["status"] == "error"
        finally:
            shutil.rmtree(folder)

    def test_run_shell_script_error_returns_action_error(self, monkeypatch):
        import subprocess as _sp
        folder = self._make_folder("""
            Rules:
              - Title: "script rule"
                Criteria:
                  - FileExtension: txt
                Actions:
                  - RunShellScript: "false"
        """, filenames=["file.txt"])
        try:
            matched, title, dest, err, results = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is True
            assert err is not None
            assert results[0]["action"] == "RunShellScript"
        finally:
            shutil.rmtree(folder)

    def test_destination_created_if_missing(self, tmp_path):
        target = tmp_path / "new_folder" / "sub"
        folder = self._make_folder(f"""
            Rules:
              - Title: "create dest"
                Criteria:
                  - FileExtension: pdf
                Actions:
                  - MoveToFolder: "{target}"
        """, filenames=["doc.pdf"])
        try:
            assert not target.exists()
            matched, title, dest, err, _ = apply_rule_by_yaml_config(folder, "doc.pdf")
            assert matched is True
            assert target.exists()
        finally:
            shutil.rmtree(folder)

    def test_unknown_criterion_no_match(self, tmp_path):
        """Unknown criterion type fails-closed — rule should not match."""
        target = tmp_path / "out"
        folder = self._make_folder(f"""
            Rules:
              - Title: "bad criterion"
                Criteria:
                  - FilenameContains: "file"
                Actions:
                  - MoveToFolder: "{target}"
        """, filenames=["file.txt"])
        try:
            matched, _, _, _, _ = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is False
        finally:
            shutil.rmtree(folder)

    def test_ai_agent_then_move_to_folder_uses_current_path(self, tmp_path):
        target = tmp_path / "out"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("do something", encoding="utf-8")
        folder = self._make_folder(f"""
            Rules:
              - Title: "agent then move"
                Criteria:
                  - FileExtension: txt
                Actions:
                  - AiAgent:
                      Model: claude
                      PromptFile: "{prompt}"
                  - MoveToFolder: "{target}"
        """, filenames=["file.txt"])
        try:
            with patch.object(_mod.AIAgentAction, "run_ai_agent", return_value=(True, "ok")) as mock_run:
                matched, _, dest, err, results = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is True
            assert err is None
            assert mock_run.call_args[0][2] == os.path.join(folder, "file.txt")
            assert dest == os.path.join(str(target), "file.txt")
            assert results[0]["action"] == "AiAgent"
            assert results[1]["action"] == "MoveToFolder"
        finally:
            shutil.rmtree(folder)

    def test_move_then_ai_agent_uses_moved_path(self, tmp_path):
        target = tmp_path / "out"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("do something", encoding="utf-8")
        folder = self._make_folder(f"""
            Rules:
              - Title: "move then agent"
                Criteria:
                  - FileExtension: txt
                Actions:
                  - MoveToFolder: "{target}"
                  - AiAgent:
                      Model: claude
                      PromptFile: "{prompt}"
        """, filenames=["file.txt"])
        try:
            with patch.object(_mod.AIAgentAction, "run_ai_agent", return_value=(True, "ok")) as mock_run:
                matched, _, dest, err, results = apply_rule_by_yaml_config(folder, "file.txt")
            moved_path = os.path.join(str(target), "file.txt")
            assert matched is True
            assert err is None
            assert dest == moved_path
            assert mock_run.call_args[0][2] == moved_path
            assert results[0]["action"] == "MoveToFolder"
            assert results[1]["action"] == "AiAgent"
        finally:
            shutil.rmtree(folder)

    def test_ai_agent_failure_stops_later_actions(self, tmp_path):
        # Break-on-first-failure: MoveToFolder after a failed AiAgent is not executed.
        target = tmp_path / "out"
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("do something", encoding="utf-8")
        folder = self._make_folder(f"""
            Rules:
              - Title: "agent failure"
                Criteria:
                  - FileExtension: txt
                Actions:
                  - AiAgent:
                      Model: claude
                      PromptFile: "{prompt}"
                  - MoveToFolder: "{target}"
        """, filenames=["file.txt"])
        try:
            with patch.object(_mod.AIAgentAction, "run_ai_agent", return_value=(False, "boom")):
                matched, _, dest, err, results = apply_rule_by_yaml_config(folder, "file.txt")
            assert matched is True
            assert err == "boom"
            assert dest is None
            assert len(results) == 1
            assert results[0]["status"] == "error"
            assert not os.path.exists(os.path.join(str(target), "file.txt"))
        finally:
            shutil.rmtree(folder)
