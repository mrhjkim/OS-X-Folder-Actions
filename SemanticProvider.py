"""Free, local, offline document classification by embedding similarity.

The paid AiRules LLM only earns its token cost on genuinely ambiguous files. Most files
are unmistakable — an invoice looks like an invoice. This classifier embeds the document
(its content and/or its filename) and each category's example utterances with a local
ONNX model (fastembed, no API key, no network after the one-time model download), and
picks the category with the highest cosine similarity. Below a threshold it returns no
match so the caller can fall through to the LLM.

EmbedSource — measured 2026-07-15 on real Korean docs:
  - "content"  classifies by topic. Great for topic-distinct categories (청구서 vs 계약서).
  - "filename" classifies by document type/format, which lives in the name
    ("주간업무보고", "설계문서"). Recovers same-topic/different-format categories that
    content embedding cannot separate.
  - "both"     concatenates filename + content (use sparingly — content can dilute the
    filename signal).
Do not compare content-cosines and filename-cosines in one argmax across rules unless you
have tuned for it; their distributions differ.
"""
import logging
import os

try:
    from fastembed import TextEmbedding
except ImportError:                       # optional dep; stage self-disables if absent
    TextEmbedding = None

# Pin the model cache under HOME. fastembed's default cache_dir is the SYSTEM TEMP dir
# (verified: $TMPDIR/fastembed_cache), which macOS can reap — forcing a multi-hundred-MB
# re-download mid Folder-Action. A fixed HOME path avoids that.
_CACHE_DIR = os.path.expanduser("~/.cache/folder-actions/fastembed")

_MODEL_CACHE = {}          # model_id -> TextEmbedding (load once per process)
_UTTERANCE_CACHE = {}      # (model_id, tuple(utterances)) -> L2-normalized matrix

_VALID_SOURCES = ("content", "filename", "both")


def classify(content, filename, rules, model_id, *, threshold, default_source="content"):
    """
    Classify a document against SemanticRules by embedding similarity.

    Args:
        content        : extracted document text (may be "")
        filename        : the file's basename (used for EmbedSource filename/both)
        rules           : [{"Title", "Utterances", "Actions", "EmbedSource"?}]
        model_id        : a fastembed-supported embedding model id
        threshold       : minimum cosine to count as a match; below → no match
        default_source  : block-level EmbedSource when a rule doesn't set its own

    Returns a dict with the SAME five keys on every path —
        matched_rule, confidence, reason, destination, error
    so the caller can copy the AiRules move+audit block unchanged (it reads result["reason"]).
    Never raises: any failure returns error set, matched_rule None.
    """
    if TextEmbedding is None:
        return _error("fastembed not installed — run pip install fastembed")
    if not rules:
        return _error("no SemanticRules rules configured")

    try:
        import numpy as np
        embedder = _get_model(model_id)
        doc_vecs = {}     # source -> normalized doc vector (embed each source at most once)

        best_title, best_score = None, -1.0
        for rule in rules:
            utterances = rule.get("Utterances") or []
            if not utterances:
                continue
            source = str(rule.get("EmbedSource", default_source)).strip().lower()
            if source not in _VALID_SOURCES:
                source = default_source
            text = _doc_text(source, content, filename)
            if not text.strip():
                continue                      # nothing to embed for this rule's source
            if source not in doc_vecs:
                doc_vecs[source] = _normalize(
                    np.array(list(embedder.embed([_prefix(model_id, text, "passage")]))[0])
                )
            mat = _utterance_matrix(embedder, model_id, utterances)   # cached, L2-normalized
            score = float(np.max(mat @ doc_vecs[source]))
            if score > best_score:
                best_title, best_score = rule.get("Title"), score
    except Exception as e:
        return _error(f"semantic classify failed: {e}")

    if best_title is None:
        return _error("no rule had usable utterances / document text")

    reason = f"cosine similarity {best_score:.3f}"
    if best_score < threshold:
        return {"matched_rule": None, "confidence": best_score, "reason": reason,
                "destination": None, "error": None}
    return {"matched_rule": best_title, "confidence": best_score, "reason": reason,
            "destination": _destination_for(best_title, rules), "error": None}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_model(model_id):
    if model_id not in _MODEL_CACHE:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        _MODEL_CACHE[model_id] = TextEmbedding(model_id, cache_dir=_CACHE_DIR)
    return _MODEL_CACHE[model_id]


def _utterance_matrix(embedder, model_id, utterances):
    """L2-normalized matrix (n_utterances x dim) for a rule, cached per (model, utterances)."""
    import numpy as np
    key = (model_id, tuple(utterances))
    if key not in _UTTERANCE_CACHE:
        prefixed = [_prefix(model_id, u, "query") for u in utterances]
        vecs = [_normalize(np.array(v)) for v in embedder.embed(prefixed)]
        _UTTERANCE_CACHE[key] = np.vstack(vecs)
    return _UTTERANCE_CACHE[key]


def _doc_text(source, content, filename):
    name = _clean_filename(filename)
    if source == "filename":
        return name
    if source == "both":
        return f"{name} {content}".strip()
    return content                            # "content" (default)


def _clean_filename(filename):
    """Strip the extension and turn separators into spaces so the name reads as words."""
    import re
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return re.sub(r"[_()\-.]+", " ", stem).strip()


def _prefix(model_id, text, kind):
    """e5 models are asymmetric and want 'query: ' / 'passage: ' prefixes; symmetric
    models (paraphrase-multilingual, bge) want none, and a wrong prefix lowers accuracy."""
    if "e5" in str(model_id).lower():
        return f"{kind}: {text}"
    return text


def _normalize(vec):
    import numpy as np
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


def _destination_for(title, rules):
    for r in rules:
        if r.get("Title") == title:
            for a in r.get("Actions", []):
                if "MoveToFolder" in a:
                    return os.path.expanduser(a["MoveToFolder"])
            break
    return None


def _error(msg):
    logging.warning(f"SemanticProvider: {msg}")
    return {
        "matched_rule": None,
        "confidence": 0.0,
        "reason": "",
        "destination": None,
        "error": msg,
    }
