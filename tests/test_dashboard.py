"""Tests for FolderActionsDashboard YAML parsing and round-trip logic."""
import json
import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from FolderActionsDashboard import (
    parse_criteria,
    parse_yaml_file,
    rules_to_yaml,
    load_logs,
    find_sources,
)


# ------------------------------------------------------------------
# parse_criteria()
# ------------------------------------------------------------------

class TestParseCriteria:
    def test_empty_returns_simple(self):
        mode, criteria, groups = parse_criteria([])
        assert mode == "simple"
        assert criteria == []
        assert groups == []

    def test_single_file_extension(self):
        mode, criteria, groups = parse_criteria([{"FileExtension": "pdf"}])
        assert mode == "simple"
        assert criteria == [{"type": "ext", "value": "pdf"}]
        assert groups == []

    def test_single_file_name_contains(self):
        mode, criteria, groups = parse_criteria([{"FileNameContains": "report"}])
        assert mode == "simple"
        assert criteria == [{"type": "name", "value": "report"}]

    def test_all_criteria_two_items_gives_and_mode(self):
        mode, criteria, groups = parse_criteria([
            {"AllCriteria": [{"FileExtension": "xlsx"}, {"FileNameContains": "weekly"}]}
        ])
        assert mode == "and"
        assert len(criteria) == 2
        assert criteria[0] == {"type": "ext", "value": "xlsx"}
        assert criteria[1] == {"type": "name", "value": "weekly"}

    def test_any_criteria_simple_items_gives_or_mode(self):
        mode, criteria, groups = parse_criteria([
            {"AnyCriteria": [{"FileExtension": "pdf"}, {"FileExtension": "docx"}]}
        ])
        assert mode == "or"
        assert len(criteria) == 2

    def test_any_criteria_all_groups_gives_groups_mode(self):
        mode, criteria, groups = parse_criteria([
            {"AnyCriteria": [
                {"AllCriteria": [{"FileExtension": "xlsx"}, {"FileNameContains": "q1"}]},
                {"AllCriteria": [{"FileExtension": "xlsx"}, {"FileNameContains": "q2"}]},
            ]}
        ])
        assert mode == "groups"
        assert len(groups) == 2
        assert groups[0] == [{"type": "ext", "value": "xlsx"}, {"type": "name", "value": "q1"}]
        assert groups[1] == [{"type": "ext", "value": "xlsx"}, {"type": "name", "value": "q2"}]

    def test_multiple_top_level_items_gives_and_mode(self):
        mode, criteria, groups = parse_criteria([
            {"FileExtension": "pdf"},
            {"FileNameContains": "invoice"},
        ])
        assert mode == "and"
        assert len(criteria) == 2

    def test_unknown_criterion_ignored(self):
        mode, criteria, groups = parse_criteria([{"UnknownKey": "value"}])
        assert criteria == []


# ------------------------------------------------------------------
# parse_yaml_file()
# ------------------------------------------------------------------

class TestParseYamlFile:
    def _write_yaml(self, content):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def test_simple_rule(self):
        path = self._write_yaml("""
Rules:
  - Title: "PDFs"
    Criteria:
      - FileExtension: pdf
    Actions:
      - MoveToFolder: ~/Documents/PDFs/
""")
        try:
            rules, ai_rules = parse_yaml_file(path)
            assert len(rules) == 1
            assert rules[0]["title"] == "PDFs"
            assert rules[0]["dest"] == "~/Documents/PDFs/"
            assert rules[0]["mode"] == "simple"
            assert ai_rules is None
        finally:
            os.unlink(path)

    def test_ai_rules_parsed(self):
        path = self._write_yaml("""
Rules: []
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "Tax docs"
      Description: "Tax receipts"
      Actions:
        - MoveToFolder: ~/Finance/
""")
        try:
            rules, ai_rules = parse_yaml_file(path)
            assert ai_rules is not None
            assert ai_rules["model"] == "llama3.2"
            assert len(ai_rules["rules"]) == 1
            assert ai_rules["rules"][0]["title"] == "Tax docs"
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        rules, ai_rules = parse_yaml_file("/nonexistent/path/.FolderActions.yaml")
        assert rules == []
        assert ai_rules is None

    def test_invalid_yaml_returns_empty(self):
        path = self._write_yaml("{ invalid yaml: [unclosed")
        try:
            rules, ai_rules = parse_yaml_file(path)
            assert rules == []
        finally:
            os.unlink(path)


# ------------------------------------------------------------------
# rules_to_yaml() round-trip
# ------------------------------------------------------------------

class TestRulesToYaml:
    def test_simple_ext_round_trip(self):
        rules = [{
            "title": "PDFs",
            "mode": "simple",
            "criteria": [{"type": "ext", "value": "pdf"}],
            "groups": [],
            "dest": "~/Documents/PDFs/",
        }]
        text = rules_to_yaml(rules, None)
        parsed = yaml.safe_load(text)
        assert parsed["Rules"][0]["Title"] == "PDFs"
        assert parsed["Rules"][0]["Criteria"] == [{"FileExtension": "pdf"}]
        assert parsed["Rules"][0]["Actions"] == [{"MoveToFolder": "~/Documents/PDFs/"}]

    def test_and_mode_round_trip(self):
        rules = [{
            "title": "Weekly reports",
            "mode": "and",
            "criteria": [{"type": "ext", "value": "xlsx"}, {"type": "name", "value": "weekly"}],
            "groups": [],
            "dest": "~/Reports/",
        }]
        text = rules_to_yaml(rules, None)
        parsed = yaml.safe_load(text)
        criteria = parsed["Rules"][0]["Criteria"]
        assert criteria == [{"AllCriteria": [{"FileExtension": "xlsx"}, {"FileNameContains": "weekly"}]}]

    def test_or_mode_round_trip(self):
        rules = [{
            "title": "Docs or PDFs",
            "mode": "or",
            "criteria": [{"type": "ext", "value": "pdf"}, {"type": "ext", "value": "docx"}],
            "groups": [],
            "dest": "~/Docs/",
        }]
        text = rules_to_yaml(rules, None)
        parsed = yaml.safe_load(text)
        criteria = parsed["Rules"][0]["Criteria"]
        assert criteria == [{"AnyCriteria": [{"FileExtension": "pdf"}, {"FileExtension": "docx"}]}]

    def test_groups_mode_round_trip(self):
        rules = [{
            "title": "Quarterly reports",
            "mode": "groups",
            "criteria": [],
            "groups": [
                [{"type": "ext", "value": "xlsx"}, {"type": "name", "value": "q1"}],
                [{"type": "ext", "value": "xlsx"}, {"type": "name", "value": "q2"}],
            ],
            "dest": "~/Quarterly/",
        }]
        text = rules_to_yaml(rules, None)
        parsed = yaml.safe_load(text)
        criteria = parsed["Rules"][0]["Criteria"]
        assert len(criteria) == 1
        assert "AnyCriteria" in criteria[0]
        assert len(criteria[0]["AnyCriteria"]) == 2

    def test_ai_rules_serialized(self):
        ai_rules = {
            "model": "llama3.2",
            "confidenceThreshold": 0.8,
            "timeoutSeconds": 60,
            "rules": [{"title": "Tax", "description": "Tax docs", "dest": "~/Finance/"}],
        }
        text = rules_to_yaml([], ai_rules)
        parsed = yaml.safe_load(text)
        assert "AiRules" in parsed
        assert parsed["AiRules"]["Model"] == "llama3.2"
        # TimeoutSeconds=60 is default, should be omitted
        assert "TimeoutSeconds" not in parsed["AiRules"]

    def test_non_default_timeout_included(self):
        ai_rules = {
            "model": "llama3.2",
            "confidenceThreshold": 0.8,
            "timeoutSeconds": 120,
            "rules": [{"title": "Tax", "description": "Tax docs", "dest": "~/Finance/"}],
        }
        text = rules_to_yaml([], ai_rules)
        parsed = yaml.safe_load(text)
        assert parsed["AiRules"]["TimeoutSeconds"] == 120


# ------------------------------------------------------------------
# load_logs()
# ------------------------------------------------------------------

class TestLoadLogs:
    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        import FolderActionsDashboard as d
        monkeypatch.setattr(d, "LOG_DIR", str(tmp_path / "nonexistent"))
        assert d.load_logs() == []

    def test_loads_valid_entries(self, tmp_path, monkeypatch):
        import FolderActionsDashboard as d
        monkeypatch.setattr(d, "LOG_DIR", str(tmp_path))
        log_file = tmp_path / "2026-04.jsonl"
        log_file.write_text(
            '{"ts":"2026-04-02T00:00:00","file":"test.pdf","rule":"PDFs","status":"moved","source":"/tmp"}\n'
            '{"ts":"2026-04-02T00:01:00","file":"skip.pdf","status":"intent"}\n'
        )
        entries = d.load_logs()
        assert len(entries) == 1
        assert entries[0]["file"] == "test.pdf"

    def test_caps_at_max_entries(self, tmp_path, monkeypatch):
        import FolderActionsDashboard as d
        monkeypatch.setattr(d, "LOG_DIR", str(tmp_path))
        monkeypatch.setattr(d, "MAX_LOG_ENTRIES", 3)
        log_file = tmp_path / "2026-04.jsonl"
        lines = [f'{{"ts":"2026-04-02T00:0{i}:00","file":"f{i}.pdf","status":"moved"}}' for i in range(5)]
        log_file.write_text("\n".join(lines) + "\n")
        entries = d.load_logs()
        assert len(entries) == 3
        # Should be the last 3 (sorted by ts)
        assert entries[-1]["file"] == "f4.pdf"
