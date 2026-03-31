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
    resp.json.return_value = {"response": inner}
    resp.raise_for_status.return_value = None
    return resp


class TestAIProvider:
    def test_stream_false_and_format_json_in_request(self):
        """Ollama request must include stream=false and format=json."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _mock_response("Tax documents", 0.95)
            AIProvider.query("some text", SAMPLE_RULES, "llama3.2")
            call_kwargs = mock_post.call_args
            body = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
            assert body["stream"] is False
            assert body["format"] == "json"

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
        resp.json.return_value = {"response": "not valid json {{{"}
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
