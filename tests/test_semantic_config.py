"""Config plumbing for SemanticRules:
  - .FolderActions._load_yaml_config load-time validation (typo hints, invalid-rule drop, Model required)
  - FolderActionsDashboard parse/serialize round-trip
"""
import importlib.util
import os
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_spec = importlib.util.spec_from_file_location(
    "FolderActions",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".FolderActions.py"),
)
_fa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fa)

from FolderActionsDashboard import parse_yaml_file, rules_to_yaml


def _load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return _fa._load_yaml_config(path)
    finally:
        os.unlink(path)


class TestLoadTimeValidation:
    def test_semanticrules_is_a_known_top_level_key(self, caplog):
        with caplog.at_level("WARNING"):
            cfg = _load("""
SemanticRules:
  Model: some-model
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert "AiRules" not in "".join(caplog.messages)   # no "did you mean 'AiRules'?" noise
        assert "SemanticRules" in cfg

    def test_unknown_subkey_gets_hint(self, caplog):
        with caplog.at_level("WARNING"):
            _load("""
SemanticRules:
  Model: m
  SimilarityThreshhold: 0.8
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")   # misspelled threshold
        assert any("Unknown SemanticRules key 'SimilarityThreshhold'" in m and "SimilarityThreshold" in m
                   for m in caplog.messages)

    def test_missing_model_drops_section(self):
        cfg = _load("""
SemanticRules:
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert "SemanticRules" not in cfg

    def test_untitled_rule_dropped(self):
        cfg = _load("""
SemanticRules:
  Model: m
  Rules:
    - Utterances: ["no title"]
      Actions: [MoveToFolder: ~/x]
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert [r["Title"] for r in cfg["SemanticRules"]["Rules"]] == ["청구서"]

    def test_rule_without_utterances_dropped(self):
        cfg = _load("""
SemanticRules:
  Model: m
  Rules:
    - Title: "빈규칙"
      Actions: [MoveToFolder: ~/x]
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert [r["Title"] for r in cfg["SemanticRules"]["Rules"]] == ["청구서"]


class TestDashboardRoundTrip:
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        return f.name

    def test_parse_returns_three_tuple(self):
        path = self._write("Rules: []\n")
        try:
            out = parse_yaml_file(path)
            assert len(out) == 3
        finally:
            os.unlink(path)

    def test_semantic_round_trip_preserves_fields(self):
        path = self._write("""
Rules: []
SemanticRules:
  Model: paraphrase-multilingual-MiniLM-L12-v2
  SimilarityThreshold: 0.55
  EmbedSource: content
  Rules:
    - Title: "청구서"
      Utterances:
        - "청구 금액 세금계산서"
        - "영수증 결제"
      Actions:
        - MoveToFolder: ~/Documents/Invoices
    - Title: "설계문서"
      EmbedSource: filename
      Utterances:
        - "설계문서 아키텍처"
      Actions:
        - MoveToFolder: ~/Documents/Design
""")
        try:
            rules, ai_rules, sem = parse_yaml_file(path)
            assert sem["model"] == "paraphrase-multilingual-MiniLM-L12-v2"
            assert sem["similarityThreshold"] == 0.55
            assert sem["rules"][0]["utterances"] == ["청구 금액 세금계산서", "영수증 결제"]
            assert sem["rules"][1]["embedSource"] == "filename"

            out = yaml.safe_load(rules_to_yaml(rules, ai_rules, sem))
            sr = out["SemanticRules"]
            assert sr["Model"] == "paraphrase-multilingual-MiniLM-L12-v2"
            assert sr["SimilarityThreshold"] == 0.55
            assert sr["Rules"][0]["Utterances"] == ["청구 금액 세금계산서", "영수증 결제"]
            assert sr["Rules"][1]["EmbedSource"] == "filename"      # per-rule override survives
            assert "EmbedSource" not in sr["Rules"][0]              # inherits block default, not emitted
        finally:
            os.unlink(path)

    def test_no_semantic_rules_omits_section(self):
        path = self._write("Rules: []\nAiRules:\n  Model: llama3.2\n  Rules:\n"
                           "    - Title: T\n      Description: d\n      Actions: [MoveToFolder: ~/x]\n")
        try:
            rules, ai_rules, sem = parse_yaml_file(path)
            assert sem is None
            out = yaml.safe_load(rules_to_yaml(rules, ai_rules, sem))
            assert "SemanticRules" not in out
        finally:
            os.unlink(path)
