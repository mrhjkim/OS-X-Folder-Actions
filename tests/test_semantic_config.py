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

    def test_rule_without_destination_dropped(self):
        # Matches internally but has no MoveToFolder → would fall through anyway. Drop it.
        cfg = _load("""
SemanticRules:
  Model: m
  Rules:
    - Title: "목적지없음"
      Utterances: ["u"]
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert [r["Title"] for r in cfg["SemanticRules"]["Rules"]] == ["청구서"]

    def test_non_numeric_threshold_does_not_crash(self):
        cfg = _load("""
SemanticRules:
  Model: m
  SimilarityThreshold: high
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert "SemanticRules" in cfg                         # not crashed
        assert "SimilarityThreshold" not in cfg["SemanticRules"]  # bad value dropped → default

    def test_rules_not_a_list_drops_section_no_crash(self):
        cfg = _load("SemanticRules:\n  Model: m\n  Rules: notalist\n")
        assert "SemanticRules" not in cfg                     # dropped, no AttributeError

    def test_similaritymargin_is_a_known_key(self, caplog):
        with caplog.at_level("WARNING"):
            cfg = _load("""
SemanticRules:
  Model: m
  SimilarityMargin: 0.05
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert not any("SimilarityMargin" in m and "Unknown" in m for m in caplog.messages)
        assert cfg["SemanticRules"]["SimilarityMargin"] == 0.05

    def test_non_numeric_margin_does_not_crash(self):
        cfg = _load("""
SemanticRules:
  Model: m
  SimilarityMargin: wide
  Rules:
    - Title: "청구서"
      Utterances: ["청구 금액"]
      Actions: [MoveToFolder: ~/Inv]
""")
        assert "SemanticRules" in cfg                            # not crashed
        assert "SimilarityMargin" not in cfg["SemanticRules"]    # bad value dropped → default 0.0


class TestStage2Runtime:
    """Drive item_added_to_folder with a mocked classify — the move/audit/fallthrough block."""

    def _setup(self, tmp_path, dest):
        work = tmp_path / "watched"
        work.mkdir()
        (work / "f.txt").write_text("x", encoding="utf-8")
        (work / ".FolderActions.yaml").write_text(f"""
Rules: []
SemanticRules:
  Model: m
  Rules:
    - Title: "T"
      Utterances: ["u"]
      Actions:
        - MoveToFolder: {dest}
Audit: {{Enabled: false}}
""", encoding="utf-8")
        return work

    def test_match_moves_file(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        work = self._setup(tmp_path, dest)
        monkeypatch.setattr(_fa.SemanticProvider, "classify",
            lambda *a, **k: {"matched_rule": "T", "confidence": 0.9, "reason": "cos 0.9",
                             "destination": str(dest), "error": None})
        _fa.item_added_to_folder(str(work), "f.txt")
        assert (dest / "f.txt").exists()
        assert not (work / "f.txt").exists()

    def test_move_failure_falls_through_no_crash(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        work = self._setup(tmp_path, dest)
        monkeypatch.setattr(_fa.SemanticProvider, "classify",
            lambda *a, **k: {"matched_rule": "T", "confidence": 0.9, "reason": "r",
                             "destination": str(dest), "error": None})
        monkeypatch.setattr(_fa.shutil, "move",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        _fa.item_added_to_folder(str(work), "f.txt")     # must not raise
        assert (work / "f.txt").exists()                 # not moved; fell through

    def test_below_threshold_falls_through(self, monkeypatch, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        work = self._setup(tmp_path, dest)
        monkeypatch.setattr(_fa.SemanticProvider, "classify",
            lambda *a, **k: {"matched_rule": None, "confidence": 0.3, "reason": "r",
                             "destination": None, "error": None})
        _fa.item_added_to_folder(str(work), "f.txt")
        assert (work / "f.txt").exists()                 # stayed put


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

    def test_filename_stopwords_round_trip(self):
        path = self._write("""
Rules: []
SemanticRules:
  Model: m
  EmbedSource: filename
  FilenameStopwords:
    - 연구개발본부
    - 개발3팀
    - 전자 직책자
  Rules:
    - Title: "주간보고"
      Utterances: ["주간업무보고"]
      Actions:
        - MoveToFolder: ~/Documents/주간업무
""")
        try:
            rules, ai_rules, sem = parse_yaml_file(path)
            assert sem["filenameStopwords"] == ["연구개발본부", "개발3팀", "전자 직책자"]
            out = yaml.safe_load(rules_to_yaml(rules, ai_rules, sem))
            assert out["SemanticRules"]["FilenameStopwords"] == ["연구개발본부", "개발3팀", "전자 직책자"]
        finally:
            os.unlink(path)

    def test_empty_stopwords_omitted(self):
        path = self._write("""
Rules: []
SemanticRules:
  Model: m
  EmbedSource: filename
  Rules:
    - Title: "주간보고"
      Utterances: ["주간업무보고"]
      Actions:
        - MoveToFolder: ~/x
""")
        try:
            rules, ai_rules, sem = parse_yaml_file(path)
            assert sem["filenameStopwords"] == []
            out = yaml.safe_load(rules_to_yaml(rules, ai_rules, sem))
            assert "FilenameStopwords" not in out["SemanticRules"]   # byte-identical round-trip
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
