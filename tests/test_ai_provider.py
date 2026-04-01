"""Tests for AIProvider — mocks Ollama with unittest.mock."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import AIProvider

SAMPLE_RULES = [
    {
        "Title": "Tax documents",
        "Description": "Tax documents, receipts, or financial records",
        "Actions": [{"MoveToFolder": "~/Documents/Finance/Tax/"}],
    },
    {
        "Title": "Work contracts",
        "Description": "Contracts, NDAs, offer letters",
        "Actions": [{"MoveToFolder": "~/Documents/Work/Contracts/"}],
    },
]


def _mock_response(matched_rule, confidence, reason="test reason"):
    inner = json.dumps({
        "matched_rule": matched_rule,
        "confidence": confidence,
        "reason": reason,
    })
    resp = MagicMock()
    resp.json.return_value = {"message": {"role": "assistant", "content": inner}}
    resp.raise_for_status.return_value = None
    return resp


class TestAIProvider:
    def test_stream_false_and_format_json_in_request(self):
        """Ollama /api/chat request must include messages, stream=false, format=json."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.95)
            AIProvider.query("some text", SAMPLE_RULES, "llama3.2")
            call_kwargs = mock_post.call_args
            body = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
            assert body["stream"] is False
            assert body["format"] == "json"
            assert "messages" in body
            assert body["messages"][0]["role"] == "user"

    def test_successful_match_returns_destination(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.94)
            result = AIProvider.query("tax invoice content", SAMPLE_RULES, "llama3.2")
        assert result["error"] is None
        assert result["matched_rule"] == "Tax documents"
        assert result["confidence"] == 0.94
        assert result["destination"] is not None
        assert "Finance/Tax" in result["destination"]

    def test_ollama_unavailable_returns_error_no_exception(self):
        with patch("requests.post", side_effect=ConnectionRefusedError("refused")):
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is not None
        assert result["matched_rule"] is None
        # Must not raise

    def test_malformed_json_returns_error(self):
        resp = MagicMock()
        resp.json.return_value = {"message": {"role": "assistant", "content": "not valid json {{{"}}
        resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=resp):
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is not None
        assert result["matched_rule"] is None

    def test_confidence_below_threshold_not_handled_in_provider(self):
        """AIProvider returns low confidence — threshold check is caller's job."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.3)
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is None
        assert result["confidence"] == 0.3  # provider doesn't apply threshold

    def test_matched_rule_not_in_titles_returns_error(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Nonexistent Rule", 0.9)
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is not None
        assert result["matched_rule"] is None

    def test_confidence_out_of_range_returns_error(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 1.5)
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is not None

    def test_null_matched_rule_is_valid(self):
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response(None, 0.0)
            result = AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
        assert result["error"] is None
        assert result["matched_rule"] is None

    def test_custom_timeout_is_passed_to_requests(self):
        """TimeoutSeconds from YAML should be forwarded to requests.post."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.9)
            AIProvider.query("snippet", SAMPLE_RULES, "llama3.2", timeout=120)
            call_kwargs = mock_post.call_args
            assert call_kwargs[1]["timeout"] == 120

    def test_default_timeout_is_60(self):
        """Default timeout should be 60s, not the original 10s."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.9)
            AIProvider.query("snippet", SAMPLE_RULES, "llama3.2")
            call_kwargs = mock_post.call_args
            assert call_kwargs[1]["timeout"] == 60

    def test_prose_response_with_embedded_json_is_extracted(self):
        """Models that ignore format:json but embed JSON in prose should still work."""
        inner_json = json.dumps({"matched_rule": "Tax documents", "confidence": 0.9, "reason": "tax content"})
        prose = f"Here is the answer: {inner_json} Hope that helps."
        resp = MagicMock()
        resp.json.return_value = {"message": {"role": "assistant", "content": prose}}
        resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=resp):
            result = AIProvider.query("tax invoice", SAMPLE_RULES, "llama3.2")
        assert result["error"] is None
        assert result["matched_rule"] == "Tax documents"

    def test_pure_prose_response_returns_error(self):
        """Models returning pure prose with no JSON should return an error gracefully."""
        resp = MagicMock()
        resp.json.return_value = {"message": {"role": "assistant", "content": "We have a task. No JSON here."}}
        resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=resp):
            result = AIProvider.query("tax invoice", SAMPLE_RULES, "llama3.2")
        assert result["error"] is not None
        assert result["matched_rule"] is None

    def test_empty_response_content_returns_error(self):
        """Chat model returning empty content (gpt-oss:20b style) should return error."""
        resp = MagicMock()
        resp.json.return_value = {
            "model": "gpt-oss:20b", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": ""},
        }
        resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=resp):
            result = AIProvider.query("tax invoice", SAMPLE_RULES, "gpt-oss:20b")
        assert result["error"] is not None
        assert result["matched_rule"] is None
