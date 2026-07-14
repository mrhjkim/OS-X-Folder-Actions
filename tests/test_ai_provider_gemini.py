"""Tests for the Gemini API backend of AIProvider.

Everything is mocked: requests.post and time.sleep. No network, no API key, no waiting.

Coverage map (numbers match the design doc test plan):
    2-3   provider dispatch + header/URL hygiene
    4-8   key resolution order and failure modes
    9,31  rate-limit and error paths never leak the key
    11-14 schema validation reuse + strict JSON
    15,20 unknown provider hint, requests missing
    16    timeout
    19    junk Model cannot forge a URL
    21-23 enum schema + __NO_MATCH__ sentinel
    24-25 key file permission warning
    28-30,32  HTTP error surfacing + 429 retry with Retry-After cap
    33    per-provider default timeout
Plus the Ollama empty-response regression guard (design test 1).
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import AIProvider

RULES = [
    {"Title": "청구서", "Description": "인보이스, 영수증",
     "Actions": [{"MoveToFolder": "~/Documents/Invoices/"}]},
    {"Title": "계약서", "Description": "NDA, 오퍼레터",
     "Actions": [{"MoveToFolder": "~/Documents/Contracts/"}]},
]

KEY = "AIzaTESTKEY1234567890"


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """Every test starts with GEMINI_API_KEY unset unless it sets it itself."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """429 retry must never actually sleep in tests. Record the requested waits."""
    waits = []
    monkeypatch.setattr(AIProvider.time, "sleep", lambda s: waits.append(s))
    return waits


def _ok(matched_rule, confidence=0.9, reason="r"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    inner = json.dumps({"matched_rule": matched_rule,
                        "confidence": confidence, "reason": reason})
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": inner}]}}]}
    return resp


def _http_error(status, message, json_body=True, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    if json_body:
        resp.json.return_value = {"error": {"message": message}}
    else:
        resp.json.side_effect = ValueError("no json")
        resp.text = message
    return resp


def _keyfile(tmp_path, content=KEY + "\n", mode=0o600):
    p = tmp_path / "gemini.key"
    p.write_text(content, encoding="utf-8")
    os.chmod(p, mode)
    return str(p)


# ------------------------------------------------------------------
# Provider dispatch + request hygiene
# ------------------------------------------------------------------

class TestDispatch:
    def test_provider_gemini_uses_header_auth(self, monkeypatch):        # test 2
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "gemini-3.5-flash", provider="gemini")
        assert mp.call_args.kwargs["headers"]["x-goog-api-key"] == KEY

    def test_url_has_no_key_query_param(self, monkeypatch):              # test 3
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "gemini-3.5-flash", provider="gemini")
        url = mp.call_args.args[0]
        assert "key=" not in url
        assert KEY not in url

    def test_absent_provider_stays_ollama(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": json.dumps(
            {"matched_rule": "청구서", "confidence": 0.9, "reason": "r"})}}
        with patch("requests.post", return_value=resp) as mp:
            AIProvider.query("x", RULES, "llama3.2")
        assert mp.call_args.args[0] == AIProvider.OLLAMA_URL

    def test_unknown_provider_hints(self):                              # test 15
        r = AIProvider.query("x", RULES, "m", provider="gemni")
        assert r["error"] is not None
        assert "did you mean 'gemini'" in r["error"]

    def test_requests_missing(self, monkeypatch):                       # test 20
        monkeypatch.setattr(AIProvider, "requests", None)
        r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert "requests library not installed" in r["error"]


# ------------------------------------------------------------------
# Key resolution
# ------------------------------------------------------------------

class TestKeyResolution:
    def test_env_wins_file_never_opened(self, monkeypatch):             # test 4
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        opened = []
        real_open = open
        monkeypatch.setattr("builtins.open",
                            lambda *a, **k: opened.append(a[0]) or real_open(*a, **k))
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "m", provider="gemini",
                             api_key_file="/nonexistent/should-not-open")
        assert "/nonexistent/should-not-open" not in opened
        assert mp.call_args.kwargs["headers"]["x-goog-api-key"] == KEY

    def test_file_read_and_newline_stripped(self, tmp_path):            # test 5
        kf = _keyfile(tmp_path, content=KEY + "\n")
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "m", provider="gemini", api_key_file=kf)
        assert mp.call_args.kwargs["headers"]["x-goog-api-key"] == KEY  # no trailing \n

    def test_no_env_no_file_errors_no_crash(self):                      # test 6
        r = AIProvider.query("x", RULES, "m", provider="gemini", api_key_file=None)
        assert r["error"] is not None
        assert "ApiKeyFile not configured" in r["error"]
        assert r["matched_rule"] is None

    def test_missing_file_errors(self, tmp_path):                       # test 7
        r = AIProvider.query("x", RULES, "m", provider="gemini",
                             api_key_file=str(tmp_path / "nope.key"))
        assert r["error"] is not None
        assert "cannot read" in r["error"]

    def test_empty_file_errors(self, tmp_path):                         # test 8
        kf = _keyfile(tmp_path, content="   \n")
        r = AIProvider.query("x", RULES, "m", provider="gemini", api_key_file=kf)
        assert "is empty" in r["error"]

    def test_group_readable_warns_but_continues(self, tmp_path, caplog):  # test 24
        kf = _keyfile(tmp_path, mode=0o644)
        with caplog.at_level("WARNING"):
            with patch("requests.post", return_value=_ok("청구서")):
                r = AIProvider.query("x", RULES, "m", provider="gemini", api_key_file=kf)
        assert r["error"] is None                       # continues
        assert any("group/world readable" in m for m in caplog.messages)

    def test_owner_only_no_warning(self, tmp_path, caplog):             # test 25
        kf = _keyfile(tmp_path, mode=0o600)
        with caplog.at_level("WARNING"):
            with patch("requests.post", return_value=_ok("청구서")):
                AIProvider.query("x", RULES, "m", provider="gemini", api_key_file=kf)
        assert not any("readable" in m for m in caplog.messages)


# ------------------------------------------------------------------
# Schema + no-match sentinel
# ------------------------------------------------------------------

class TestSchema:
    def test_enum_is_titles_plus_sentinel(self):                       # test 21
        schema = AIProvider._gemini_schema(RULES)
        enum = schema["properties"]["matched_rule"]["enum"]
        assert enum == ["청구서", "계약서", "__NO_MATCH__"]
        assert schema["required"] == ["matched_rule", "confidence", "reason"]

    def test_schema_sent_in_request(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "m", provider="gemini")
        gen = mp.call_args.kwargs["json"]["generationConfig"]
        assert gen["responseMimeType"] == "application/json"
        assert gen["responseSchema"]["properties"]["matched_rule"]["enum"][-1] == "__NO_MATCH__"

    def test_no_match_becomes_none(self, monkeypatch):                 # test 22
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("__NO_MATCH__", confidence=0.0)):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["error"] is None
        assert r["matched_rule"] is None
        assert r["destination"] is None

    def test_valid_match_enriched(self, monkeypatch):                  # test 11
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서", confidence=0.92)):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["matched_rule"] == "청구서"
        assert r["confidence"] == 0.92
        assert "Invoices" in r["destination"]

    def test_rogue_title_rejected(self, monkeypatch):                  # test 12
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("Invoices")):     # not a real Title
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["error"] is not None
        assert r["matched_rule"] is None

    def test_confidence_out_of_range(self, monkeypatch):               # test 13
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서", confidence=1.5)):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["error"] is not None

    def test_strict_prose_errors_no_fallback(self, monkeypatch):       # test 14
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = {"candidates": [{"content": {
            "parts": [{"text": "Sure! Here is {the answer} in prose."}]}}]}
        with patch("requests.post", return_value=resp):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["error"] is not None
        assert "unparseable JSON" in r["error"]


# ------------------------------------------------------------------
# HTTP errors, retry, URL safety, timeout
# ------------------------------------------------------------------

class TestHttp:
    def test_404_surfaces_google_message(self, monkeypatch):           # test 28
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        msg = "models/gemini-3.5-flesh is not found for API version v1beta"
        with patch("requests.post", return_value=_http_error(404, msg)):
            r = AIProvider.query("x", RULES, "gemini-3.5-flesh", provider="gemini")
        assert "is not found" in r["error"]
        assert KEY not in r["error"]

    def test_500_non_json_body_truncated(self, monkeypatch):           # test 29
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        body = "Internal Server Error " * 50
        with patch("requests.post",
                   return_value=_http_error(500, body, json_body=False)):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert "gemini HTTP 500" in r["error"]
        assert len(r["error"]) < 300

    def test_429_then_200_retries_once(self, monkeypatch, _no_sleep):  # test 30
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        seq = [_http_error(429, "slow down", headers={"Retry-After": "7"}), _ok("청구서")]
        with patch("requests.post", side_effect=seq) as mp:
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert mp.call_count == 2
        assert _no_sleep == [7]
        assert r["matched_rule"] == "청구서"

    def test_429_twice_errors_no_key_leak(self, monkeypatch):          # test 31
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        seq = [_http_error(429, "slow"), _http_error(429, "slow")]
        with patch("requests.post", side_effect=seq):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert "gemini HTTP 429" in r["error"]
        assert KEY not in r["error"]

    def test_retry_after_capped_at_30(self, monkeypatch, _no_sleep):   # test 32
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        seq = [_http_error(429, "slow", headers={"Retry-After": "600"}), _ok("청구서")]
        with patch("requests.post", side_effect=seq):
            AIProvider.query("x", RULES, "m", provider="gemini")
        assert _no_sleep == [30]

    def test_junk_model_is_percent_encoded(self, monkeypatch):         # test 19
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "../../evil model", provider="gemini")
        url = mp.call_args.args[0]
        assert url.startswith("https://generativelanguage.googleapis.com/")
        assert "/../.." not in url
        assert "evil model" not in url          # space + slash encoded

    def test_timeout_errors_gracefully(self, monkeypatch):             # test 16
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        import requests as _rq
        with patch("requests.post", side_effect=_rq.exceptions.Timeout("timed out")):
            r = AIProvider.query("x", RULES, "m", provider="gemini")
        assert r["error"] is not None
        assert r["matched_rule"] is None


# ------------------------------------------------------------------
# Per-provider default timeout (test 33)
# ------------------------------------------------------------------

class TestTimeout:
    def test_gemini_default_is_20(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "m", provider="gemini")
        assert mp.call_args.kwargs["timeout"] == 20

    def test_ollama_default_is_60(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"message": {"content": json.dumps(
            {"matched_rule": "청구서", "confidence": 0.9, "reason": "r"})}}
        with patch("requests.post", return_value=resp) as mp:
            AIProvider.query("x", RULES, "llama3.2")
        assert mp.call_args.kwargs["timeout"] == 60

    def test_explicit_timeout_wins(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        with patch("requests.post", return_value=_ok("청구서")) as mp:
            AIProvider.query("x", RULES, "m", provider="gemini", timeout=45)
        assert mp.call_args.kwargs["timeout"] == 45


# ------------------------------------------------------------------
# Ollama regression guard (design test 1)
# ------------------------------------------------------------------

class TestOllamaRegression:
    def test_empty_response_diagnostic_survives(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"model": "gpt-oss:20b", "done": True,
                                  "message": {"role": "assistant", "content": ""}}
        with patch("requests.post", return_value=resp):
            r = AIProvider.query("x", RULES, "gpt-oss:20b")
        assert r["error"] is not None
        # The rich diagnostic (outer keys + body preview) must not be flattened away.
        assert "outer keys" in r["error"]
        assert "done" in r["error"]
