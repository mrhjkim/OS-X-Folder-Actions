import json
import logging
import os

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_TIMEOUT_SECONDS = 60


def query(snippet: str, rules: list, model: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """
    Classify a file against a list of AI rules using Ollama.

    Args:
        snippet : text extracted from the file (may be "")
        rules   : list of dicts from AiRules.Rules (each has Title, Description, Actions)
        model   : Ollama model name (e.g. "llama3.2")

    Returns dict with keys:
        matched_rule : str | None   — Title from AiRules.Rules, or None
        confidence   : float        — 0.0 – 1.0
        reason       : str          — one sentence
        destination  : str | None   — expanded MoveToFolder path, or None
        error        : str | None   — set on any exception; caller falls through to Stage 3
    """
    try:
        import requests
    except ImportError:
        return _error("requests library not installed — run pip install requests")

    prompt = _build_prompt(snippet, rules)

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        return _error(f"Ollama request failed: {e}")

    try:
        outer = resp.json()
        # /api/chat returns {"message": {"role": "assistant", "content": "..."}}
        message = outer.get("message", {})
        raw_response = message.get("content", "") if isinstance(message, dict) else ""
        if not raw_response:
            outer_keys = list(outer.keys()) if isinstance(outer, dict) else repr(outer)
            return _error(
                f"Ollama chat response is empty — "
                f"outer keys: {outer_keys}, body: {str(outer)[:400]}"
            )
        try:
            result = json.loads(raw_response)
        except (json.JSONDecodeError, ValueError):
            # Model ignored format:"json" and returned prose — extract outermost JSON object
            start = raw_response.find('{')
            end = raw_response.rfind('}')
            if start == -1 or end <= start:
                return _error(f"Ollama returned prose with no JSON — raw: {raw_response[:300]}")
            try:
                result = json.loads(raw_response[start:end + 1])
            except (json.JSONDecodeError, ValueError) as e2:
                return _error(f"Ollama JSON extraction failed: {e2} — raw: {raw_response[:300]}")
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        return _error(f"Ollama response parse failed: {e} — raw: {resp.text[:200]}")

    return _validate_and_enrich(result, rules)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_prompt(snippet: str, rules: list) -> str:
    filename = ""  # caller doesn't pass filename — snippet is the context
    rules_block = "\n".join(
        f'- "{r["Title"]}": {r.get("Description", "")} → {_first_action(r)}'
        for r in rules
    )
    return (
        'OUTPUT ONLY A JSON OBJECT. No prose. No explanation. No markdown. Just JSON.\n\n'
        "You are a file classifier. Match the file content to the best rule below.\n\n"
        f'File content:\n"""\n{snippet}\n"""\n\n'
        f"Rules:\n{rules_block}\n\n"
        'Output exactly this JSON (fill in the values):\n'
        '{"matched_rule": "<rule title or null>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}\n'
        'Use JSON null (not the string "null") when no rule fits.\n'
        'STOP after the closing brace. Output nothing else.'
    )


def _first_action(rule: dict) -> str:
    actions = rule.get("Actions", [])
    if actions and "MoveToFolder" in actions[0]:
        return os.path.expanduser(actions[0]["MoveToFolder"])
    return "(unknown destination)"


def _validate_and_enrich(result: dict, rules: list) -> dict:
    """Validate schema and attach destination from matching rule."""
    matched_rule = result.get("matched_rule")
    confidence = result.get("confidence")
    reason = result.get("reason", "")

    # matched_rule must be None or a string in the rules list
    valid_titles = {r["Title"] for r in rules}
    if matched_rule is not None and matched_rule not in valid_titles:
        return _error(f"invalid response schema: matched_rule '{matched_rule}' not in rules")

    # confidence must be float in [0.0, 1.0]
    try:
        confidence = float(confidence)
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("out of range")
    except (TypeError, ValueError):
        return _error(f"invalid response schema: confidence '{confidence}' is not 0.0-1.0")

    destination = None
    if matched_rule:
        for r in rules:
            if r["Title"] == matched_rule:
                actions = r.get("Actions", [])
                if actions and "MoveToFolder" in actions[0]:
                    destination = os.path.expanduser(actions[0]["MoveToFolder"])
                break

    return {
        "matched_rule": matched_rule,
        "confidence": confidence,
        "reason": str(reason),
        "destination": destination,
        "error": None,
    }


def _error(msg: str) -> dict:
    logging.warning(f"AIProvider: {msg}")
    return {
        "matched_rule": None,
        "confidence": 0.0,
        "reason": "",
        "destination": None,
        "error": msg,
    }
