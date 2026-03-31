import json
import logging
import os

OLLAMA_URL = "http://localhost:11434/api/generate"
TIMEOUT_SECONDS = 10


def query(snippet: str, rules: list, model: str) -> dict:
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
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as e:
        return _error(f"Ollama request failed: {e}")

    try:
        outer = resp.json()
        raw_response = outer.get("response", "")
        result = json.loads(raw_response)
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
        "You are a file classifier. Return the best matching rule name and your confidence.\n"
        "If no rule fits well, return null for matched_rule.\n\n"
        f'Content: "{snippet}"\n\n'
        f"Rules:\n{rules_block}\n\n"
        'Respond ONLY as JSON:\n'
        '{"matched_rule": null, "confidence": 0.0, "reason": "<one sentence>"}\n'
        'Use null (not the string "null") when no rule fits.'
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
