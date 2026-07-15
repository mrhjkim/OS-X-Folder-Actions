"""Config plumbing for the Gemini backend:

  - .FolderActions._load_yaml_config load-time validation
      (sub-key typo hints, __NO_MATCH__ reservation, gemini-without-key skip)
  - FolderActionsDashboard round-trip preserves Provider / ApiKeyFile
      and never exposes the key contents.
"""
import importlib.util
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# .FolderActions.py has a leading dot — load it via importlib
_spec = importlib.util.spec_from_file_location(
    "FolderActions",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".FolderActions.py"),
)
_fa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fa)

from FolderActionsDashboard import parse_yaml_file, rules_to_yaml


def _load(text, monkeypatch=None):
    """Write YAML to a temp file and run it through _load_yaml_config."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return _fa._load_yaml_config(path)
    finally:
        os.unlink(path)


# ------------------------------------------------------------------
# .FolderActions load-time validation
# ------------------------------------------------------------------

class TestLoadTimeValidation:
    def test_unknown_subkey_gets_typo_hint(self, caplog):              # test 26
        with caplog.at_level("WARNING"):
            _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  ApiKeyfile: ~/x.key
""")  # lowercase f
        assert any("Unknown AiRules key 'ApiKeyfile'" in m and "ApiKeyFile" in m
                   for m in caplog.messages)

    def test_reserved_no_match_title_rejected(self, caplog, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        with caplog.at_level("ERROR"):
            _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  Rules:
    - Title: "__NO_MATCH__"
      Actions:
        - MoveToFolder: ~/x/
""")
        assert any("__NO_MATCH__" in m and "reserved" in m for m in caplog.messages)

    def test_gemini_without_key_skips_airules(self, monkeypatch):      # load-time skip
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        cfg = _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  Rules:
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        assert "AiRules" not in cfg

    def test_gemini_with_apikeyfile_kept(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        cfg = _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  ApiKeyFile: ~/.config/folder-actions/gemini.key
  Rules:
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        assert "AiRules" in cfg

    def test_gemini_with_env_key_kept(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        cfg = _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  Rules:
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        assert "AiRules" in cfg

    def test_ollama_still_needs_no_key(self):
        cfg = _load("""
AiRules:
  Model: llama3.2
  Rules:
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        assert "AiRules" in cfg

    def test_reserved_title_rule_is_dropped(self, monkeypatch):
        # Not just warned — the rule is removed so it can't shadow the sentinel.
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        cfg = _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  Rules:
    - Title: "__NO_MATCH__"
      Actions:
        - MoveToFolder: ~/x/
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        titles = [r.get("Title") for r in cfg["AiRules"]["Rules"]]
        assert titles == ["청구서"]

    def test_untitled_rule_is_dropped(self, monkeypatch):
        # A rule with no Title would crash prompt building — drop it at load time.
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        cfg = _load("""
AiRules:
  Model: gemini-3.5-flash
  Provider: gemini
  Rules:
    - Description: "no title"
      Actions:
        - MoveToFolder: ~/x/
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        titles = [r.get("Title") for r in cfg["AiRules"]["Rules"]]
        assert titles == ["청구서"]


# ------------------------------------------------------------------
# Dashboard round-trip — the field-drop guard
# ------------------------------------------------------------------

class TestDashboardRoundTrip:
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                        encoding="utf-8")
        f.write(text)
        f.close()
        return f.name

    def test_provider_and_apikeyfile_survive_round_trip(self):         # test 17
        path = self._write("""
Rules: []
AiRules:
  Provider: gemini
  Model: gemini-3.5-flash
  ApiKeyFile: ~/.config/folder-actions/gemini.key
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "청구서"
      Description: "인보이스"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        try:
            rules, ai_rules, _sem = parse_yaml_file(path)
            assert ai_rules["provider"] == "gemini"
            assert ai_rules["apiKeyFile"] == "~/.config/folder-actions/gemini.key"

            out = yaml.safe_load(rules_to_yaml(rules, ai_rules))
            assert out["AiRules"]["Provider"] == "gemini"
            assert out["AiRules"]["ApiKeyFile"] == "~/.config/folder-actions/gemini.key"
        finally:
            os.unlink(path)

    def test_ollama_round_trip_omits_provider(self):
        """An ollama config must not sprout a Provider key after a round-trip."""
        path = self._write("""
Rules: []
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "Tax"
      Description: "receipts"
      Actions:
        - MoveToFolder: ~/Finance/
""")
        try:
            rules, ai_rules, _sem = parse_yaml_file(path)
            out = yaml.safe_load(rules_to_yaml(rules, ai_rules))
            assert "Provider" not in out["AiRules"]
            assert "ApiKeyFile" not in out["AiRules"]
        finally:
            os.unlink(path)

    def test_key_contents_never_in_serialized_output(self):           # test 18
        """The dashboard round-trip carries the key file PATH, never its contents."""
        path = self._write("""
Rules: []
AiRules:
  Provider: gemini
  Model: gemini-3.5-flash
  ApiKeyFile: ~/.config/folder-actions/gemini.key
  Rules:
    - Title: "청구서"
      Actions:
        - MoveToFolder: ~/Invoices/
""")
        try:
            rules, ai_rules, _sem = parse_yaml_file(path)
            # Whatever the dashboard round-trips is a path string, not a secret.
            assert ai_rules["apiKeyFile"].endswith(".key")
            assert "AIza" not in rules_to_yaml(rules, ai_rules)
        finally:
            os.unlink(path)


# ------------------------------------------------------------------
# End-to-end: item_added_to_folder threads Provider/ApiKeyFile into query()
# ------------------------------------------------------------------

class TestEndToEndThreading:
    def test_gemini_config_routes_a_dropped_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        work = tmp_path / "watched"
        work.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        (work / "doc.txt").write_text("invoice, total due, payment terms", encoding="utf-8")
        (work / ".FolderActions.yaml").write_text(f"""
Rules: []
AiRules:
  Provider: gemini
  Model: gemini-3.5-flash
  Rules:
    - Title: "청구서"
      Description: "invoice, receipt"
      Actions:
        - MoveToFolder: {dest}
Audit: {{Enabled: false}}
""", encoding="utf-8")

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text":
            json.dumps({"matched_rule": "청구서", "confidence": 0.95, "reason": "r"})}]}}]}

        with patch("requests.post", return_value=resp) as mp:
            _fa.item_added_to_folder(str(work), "doc.txt")

        # Routed by content, and the request actually went to Gemini (provider threaded through).
        assert (dest / "doc.txt").exists()
        assert not (work / "doc.txt").exists()
        assert mp.call_args.args[0].startswith("https://generativelanguage.googleapis.com/")
