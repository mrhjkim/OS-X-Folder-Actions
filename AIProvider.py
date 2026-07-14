import json
import logging
import os
import time
import difflib
from urllib.parse import quote

try:
    import requests
except ImportError:                       # preserves the friendly "not installed" path
    requests = None

OLLAMA_URL = "http://localhost:11434/api/chat"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta"
              "/models/{model}:generateContent")

DEFAULT_TIMEOUT_SECONDS = 60      # Ollama cold start
GEMINI_DEFAULT_TIMEOUT = 60       # thinking-enabled flash models can take tens of seconds;
                                  # 20s was too tight against a real file drop. Override
                                  # per-folder with AiRules.TimeoutSeconds.
NO_MATCH = "__NO_MATCH__"         # schema sentinel; a rule may not use this title


class _MissingKey(Exception):
    """No API key could be resolved. Caught in query(), never propagates."""


class _BackendError(Exception):
    """A backend failed. Message is safe to log: it never contains the key."""


def query(snippet: str, rules: list, model: str, *,
          provider: str = "ollama", api_key_file: str | None = None,
          timeout: int | None = None) -> dict:
    """
    Classify a file against a list of AI rules.

    Backend selection:
                    ┌─ provider="ollama" (default) ─→ _backend_ollama ─→ localhost
        query() ────┤                                   (never leaves the Mac)
                    └─ provider="gemini" ────────────→ _backend_gemini ─→ Google API
                                                        (uploads file contents)

    query() only orchestrates: build prompt → call backend → parse → validate.
    Each backend returns the raw model text (a JSON string) or raises _MissingKey
    / _BackendError, both of which become a clean _error dict, never a crash.

    Args:
        snippet      : text extracted from the file (may be "")
        rules        : list of dicts from AiRules.Rules (each has Title, Description, Actions)
        model        : model name (e.g. "llama3.2" or "gemini-3.5-flash")
        provider     : "ollama" (default) or "gemini"
        api_key_file : path to a file holding the API key (gemini only)
        timeout      : per-request seconds; None → 20 for gemini, 60 for ollama

    Returns dict with keys:
        matched_rule : str | None   — Title from AiRules.Rules, or None
        confidence   : float        — 0.0 – 1.0
        reason       : str          — one sentence
        destination  : str | None   — expanded MoveToFolder path, or None
        error        : str | None   — set on any failure; caller falls through to Stage 3
    """
    if requests is None:                  # must precede dispatch, or a backend hits None.post
        return _error("requests library not installed — run pip install requests")

    name = str(provider).strip().lower()
    backend = _BACKENDS.get(name)
    if backend is None:
        hint = difflib.get_close_matches(name, list(_BACKENDS), n=1, cutoff=0.6)
        suffix = f" — did you mean '{hint[0]}'?" if hint else ""
        return _error(f"Unknown AiRules provider: {provider}{suffix}. "
                      f"Supported: {sorted(_BACKENDS)}")

    if timeout is None:
        timeout = GEMINI_DEFAULT_TIMEOUT if name == "gemini" else DEFAULT_TIMEOUT_SECONDS

    prompt = _build_prompt(snippet, rules)
    try:
        raw = backend(prompt, model, timeout, {"api_key_file": api_key_file, "rules": rules})
    except (_MissingKey, _BackendError) as e:
        return _error(str(e))
    except Exception as e:                 # network, JSON decode of the envelope, etc.
        return _error(f"{name} request failed: {e}")

    result = _parse_json(raw, strict=name in _STRICT_JSON_BACKENDS)
    if result is None:
        return _error(f"{name} returned unparseable JSON")
    if result.get("matched_rule") == NO_MATCH:   # sentinel → the None contract
        result["matched_rule"] = None
    return _validate_and_enrich(result, rules)


# ------------------------------------------------------------------
# Backends — each takes (prompt, model, timeout, cfg) and returns raw model text.
# cfg carries {"api_key_file": str|None, "rules": list}.
# ------------------------------------------------------------------

def _backend_ollama(prompt: str, model: str, timeout: int, _cfg: dict) -> str:
    """Ollama /api/chat. Prose-tolerant: query() extracts JSON via _parse_json.

    The empty-response diagnostic (outer keys + body preview) raises rather than
    returning "", so the rich message survives to the caller.
    """
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
        raise _BackendError(f"Ollama request failed: {e}") from e

    outer = resp.json()
    # /api/chat returns {"message": {"role": "assistant", "content": "..."}}
    message = outer.get("message", {})
    raw = message.get("content", "") if isinstance(message, dict) else ""
    if not raw:
        outer_keys = list(outer.keys()) if isinstance(outer, dict) else repr(outer)
        raise _BackendError(
            f"Ollama chat response is empty — "
            f"outer keys: {outer_keys}, body: {str(outer)[:400]}"
        )
    return raw


def _gemini_post(url, key, prompt, schema, timeout):
    return requests.post(
        url,
        # The key MUST travel in the header. Switch to ?key= and any requests
        # exception that echoes the URL leaks it straight into the log file.
        headers={"x-goog-api-key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        },
        timeout=timeout,
    )


def _backend_gemini(prompt: str, model: str, timeout: int, cfg: dict) -> str:
    """Gemini generateContent. responseSchema guarantees JSON, so query() parses
    it strictly (no prose fallback). matched_rule is enum-constrained to real rule
    titles plus NO_MATCH, so an invented title is impossible at the API boundary.
    """
    key = _resolve_api_key(cfg.get("api_key_file"))
    url = GEMINI_URL.format(model=quote(str(model).strip(), safe=""))  # junk Model must not forge a URL
    schema = _gemini_schema(cfg["rules"])

    resp = _gemini_post(url, key, prompt, schema, timeout)

    if resp.status_code == 429:            # a scanner dumping 20 PDFs at once
        try:
            wait = min(int(resp.headers.get("Retry-After", 5) or 5), 30)
        except (TypeError, ValueError):
            wait = 5
        logging.warning(f"AIProvider: gemini rate limited, retrying once in {wait}s")
        time.sleep(wait)
        resp = _gemini_post(url, key, prompt, schema, timeout)

    if resp.status_code != 200:
        # Google's body carries the useful text ("models/gemini-3.5-flesh is not
        # found"). raise_for_status() throws it away. The key is in the header,
        # never the body, so surfacing this is safe.
        try:
            detail = resp.json()["error"]["message"]
        except Exception:
            detail = (resp.text or "")[:200]
        raise _BackendError(f"gemini HTTP {resp.status_code}: {detail}")

    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise _BackendError(f"gemini response shape unexpected: {e}") from e


_BACKENDS = {"ollama": _backend_ollama, "gemini": _backend_gemini}
_STRICT_JSON_BACKENDS = {"gemini"}   # responseSchema guarantees conformance


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_api_key(api_key_file: str | None, env_var: str = "GEMINI_API_KEY") -> str:
    """Env var first (terminal/tests), then the key file (GUI daemon has no env).

    Raises _MissingKey on any failure so the AI stage is skipped, never crashed.
    """
    key = os.environ.get(env_var, "").strip()
    if key:
        return key
    if not api_key_file:
        raise _MissingKey(f"{env_var} unset and AiRules.ApiKeyFile not configured")
    path = os.path.expanduser(api_key_file)
    try:
        mode = os.stat(path).st_mode
        if mode & 0o077:                   # ssh refuses here; a personal tool warns
            logging.warning(
                f"AIProvider: AiRules.ApiKeyFile {path} is group/world readable "
                f"(mode {oct(mode & 0o777)}). Run: chmod 600 {path}"
            )
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError as e:
        raise _MissingKey(f"cannot read AiRules.ApiKeyFile {path}: {e.strerror}") from e
    if not key:
        raise _MissingKey(f"AiRules.ApiKeyFile {path} is empty")
    return key


def _gemini_schema(rules: list) -> dict:
    """Constrain matched_rule to real rule titles plus the no-match sentinel.

    The enum IS the whitelist. _validate_and_enrich still checks it for the Ollama
    path, which has no such constraint.
    """
    return {
        "type": "object",
        "properties": {
            "matched_rule": {"type": "string",
                             "enum": [r["Title"] for r in rules] + [NO_MATCH]},
            "confidence":   {"type": "number"},
            "reason":       {"type": "string"},
        },
        "required": ["matched_rule", "confidence", "reason"],
    }


def _parse_json(raw: str, strict: bool = False):
    """json.loads, with a prose-tolerant fallback when strict is False.

    Returns the parsed object, or None if nothing usable could be extracted.
    strict=True (gemini, responseSchema-guaranteed) skips the fallback entirely.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        if strict:
            return None
        # Model ignored format:"json" and returned prose — extract outermost {...}
        start = raw.find('{')
        end = raw.rfind('}')
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None


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
