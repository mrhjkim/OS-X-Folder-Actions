"""Tests for SemanticProvider — mocks fastembed's TextEmbedding with deterministic vectors.

No model download, no network. A fake embedder maps text to fixed vectors by keyword so
the cosine outcome is controllable:
    contains 청구/invoice → [1,0,0]
    contains 계약/contract → [0,1,0]
    otherwise (ambiguous) → [0.4,0.4,0.4]
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import SemanticProvider


def _fake_vec(t):
    if "청구" in t or "invoice" in t.lower():
        return np.array([1.0, 0.0, 0.0])
    if "계약" in t or "contract" in t.lower():
        return np.array([0.0, 1.0, 0.0])
    return np.array([0.4, 0.4, 0.4])


class FakeEmbedding:
    init_count = 0
    embedded = []            # every text ever embedded (across the process)

    def __init__(self, model_id, cache_dir=None):
        FakeEmbedding.init_count += 1
        self.model_id = model_id

    def embed(self, texts):
        for t in texts:
            FakeEmbedding.embedded.append(t)
            yield _fake_vec(t)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Fresh module caches + a fresh fake per test (module globals leak otherwise)."""
    SemanticProvider._MODEL_CACHE.clear()
    SemanticProvider._UTTERANCE_CACHE.clear()
    FakeEmbedding.init_count = 0
    FakeEmbedding.embedded = []
    monkeypatch.setattr(SemanticProvider, "TextEmbedding", FakeEmbedding)


RULES = [
    {"Title": "청구서", "Utterances": ["청구 금액 세금계산서"],
     "Actions": [{"MoveToFolder": "~/Documents/Invoices"}]},
    {"Title": "계약서", "Utterances": ["계약 서명 날인"],
     "Actions": [{"MoveToFolder": "~/Documents/Contracts"}]},
]


def _classify(content="", filename="doc.txt", rules=None, threshold=0.9, source="content"):
    return SemanticProvider.classify(content, filename, rules or RULES, "fake-model",
                                     threshold=threshold, default_source=source)


class TestClassify:
    def test_content_match(self):
        r = _classify(content="청구서 결제 내역입니다")
        assert r["error"] is None
        assert r["matched_rule"] == "청구서"
        assert r["confidence"] == pytest.approx(1.0)
        assert r["destination"].endswith("/Documents/Invoices")

    def test_below_threshold_falls_through(self):
        r = _classify(content="애매한 일반 문서")         # → [0.4,0.4,0.4]
        assert r["error"] is None
        assert r["matched_rule"] is None                  # caller falls to the LLM
        assert 0.0 < r["confidence"] < 0.9

    def test_reason_present_on_every_path(self):
        assert "reason" in _classify(content="청구 내역")           # match
        assert "reason" in _classify(content="애매")                # fallthrough
        assert "reason" in SemanticProvider.classify("", "x", [], "m", threshold=0.5)  # error


class TestEmbedSource:
    def test_filename_source_recovers_signal_content_cannot(self):
        # Content is ambiguous, but the type keyword lives in the filename.
        amb = "이번 분기 진행 상황 정리"
        assert _classify(content=amb, filename="a.txt", source="content")["matched_rule"] is None
        r = _classify(content=amb, filename="2026_청구서_최종본.pdf", source="filename")
        assert r["matched_rule"] == "청구서"

    def test_per_rule_override_beats_block_default(self):
        rules = [
            {"Title": "청구서", "Utterances": ["청구 금액"], "EmbedSource": "filename",
             "Actions": [{"MoveToFolder": "~/Invoices"}]},
        ]
        r = _classify(content="애매한 내용", filename="7월_청구서.pdf",
                      rules=rules, source="content")
        assert r["matched_rule"] == "청구서"     # rule's filename override won

    def test_both_source_concatenates(self):
        r = _classify(content="애매", filename="계약_문서.docx", source="both")
        assert r["matched_rule"] == "계약서"


class TestCaching:
    def test_model_loaded_once_across_calls(self):
        _classify(content="청구 내역")
        _classify(content="계약 문서")
        assert FakeEmbedding.init_count == 1

    def test_utterances_embedded_once(self):
        _classify(content="청구 내역")
        _classify(content="다른 청구 문서")
        # Each rule's utterance text embedded exactly once despite two classify calls.
        assert FakeEmbedding.embedded.count("청구 금액 세금계산서") == 1
        assert FakeEmbedding.embedded.count("계약 서명 날인") == 1


class TestErrors:
    def test_fastembed_missing(self, monkeypatch):
        monkeypatch.setattr(SemanticProvider, "TextEmbedding", None)
        r = _classify(content="청구 내역")
        assert "fastembed not installed" in r["error"]
        assert r["matched_rule"] is None

    def test_no_rules(self):
        r = SemanticProvider.classify("청구", "f", [], "m", threshold=0.5)
        assert r["error"] is not None

    def test_empty_content_with_content_source(self):
        r = _classify(content="", filename="x.txt", source="content")
        assert r["error"] is not None           # no usable text to embed
        assert r["matched_rule"] is None

    def test_rule_without_utterances_is_skipped(self):
        rules = [
            {"Title": "빈규칙", "Utterances": [], "Actions": [{"MoveToFolder": "~/x"}]},
            {"Title": "청구서", "Utterances": ["청구 금액"], "Actions": [{"MoveToFolder": "~/Inv"}]},
        ]
        r = _classify(content="청구 내역", rules=rules)
        assert r["matched_rule"] == "청구서"


class TestPrefix:
    def test_e5_model_prefixes_query_and_passage(self):
        SemanticProvider.classify("청구 내역", "f.txt", RULES, "intfloat/multilingual-e5-large",
                                  threshold=0.9, default_source="content")
        assert any(t.startswith("passage: ") for t in FakeEmbedding.embedded)
        assert any(t.startswith("query: ") for t in FakeEmbedding.embedded)

    def test_symmetric_model_has_no_prefix(self):
        _classify(content="청구 내역")            # fake-model, not e5
        assert not any(t.startswith(("query: ", "passage: ")) for t in FakeEmbedding.embedded)


def test_clean_filename():
    assert SemanticProvider._clean_filename("2026_청구서(최종)-v2.pdf") == "2026 청구서 최종 v2"
