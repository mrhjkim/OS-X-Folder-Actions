#!/usr/bin/env python3
"""
folder-actions dashboard — local web server for audit log viewer + rule editor.

Usage:
    folder-actions dashboard           # start server, open browser
    folder-actions dashboard --port N  # use specific port
"""

import argparse
import glob
import json
import os
import socket
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

LOG_DIR = os.path.expanduser("~/.folder-actions-log")
DEFAULT_PORT = 7373
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_LOG_ENTRIES = 2000
MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_RETROACTIVE_PREVIEW_FILES = 100
MAX_RETROACTIVE_RUN_FILES = 50

# ─── LAZY IMPORTS ─────────────────────────────────────────────────────────────

_FA_MODULE_CACHE = None  # cached after first successful load


def _load_folder_actions_module():
    """Load .FolderActions.py via importlib (leading-dot filename prevents normal import).
    Cached at module level to avoid repeated exec_module() calls and file handle leaks."""
    global _FA_MODULE_CACHE
    if _FA_MODULE_CACHE is not None:
        return _FA_MODULE_CACHE
    import importlib.util
    fa_path = os.path.join(SCRIPT_DIR, ".FolderActions.py")
    if not os.path.isfile(fa_path):
        return None
    spec = importlib.util.spec_from_file_location("FolderActions", fa_path)
    mod = importlib.util.module_from_spec(spec)
    # Inject SCRIPT_DIR so sibling modules (AuditLogger, AIProvider) can be imported
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    spec.loader.exec_module(mod)
    _FA_MODULE_CACHE = mod
    return mod


# ─── DATA LOADING ─────────────────────────────────────────────────────────────

def load_logs():
    """Load all JSONL audit log entries, sorted by timestamp."""
    if not os.path.isdir(LOG_DIR):
        return []
    entries = []
    for fpath in glob.glob(os.path.join(LOG_DIR, "*.jsonl")):
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("status") != "intent":
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    entries.sort(key=lambda e: e.get("ts", ""))
    return entries[-MAX_LOG_ENTRIES:]


def find_sources(log_entries):
    """Find .FolderActions.yaml files from source folders recorded in the log."""
    seen_set = set()
    seen_folders = []
    for e in log_entries:
        src = e.get("source")
        if src and src not in seen_set:
            seen_set.add(src)
            seen_folders.append(src)

    sources = []
    for folder in seen_folders:
        yaml_path = os.path.join(folder, ".FolderActions.yaml")
        if os.path.isfile(yaml_path):
            rules, ai_rules = parse_yaml_file(yaml_path)
            sources.append({
                "folder": folder,
                "yamlPath": yaml_path,
                "rules": rules,
                "aiRules": ai_rules,
            })
    return sources


# ─── RETROACTIVE APPLY HELPERS ────────────────────────────────────────────────

def get_processed_files(folder_path: str, rule_title: str) -> dict:
    """
    Return {filename: last_processed_iso} for files processed by rule_title in folder.
    Delegates log path to AuditLogger to avoid duplicating the naming formula.
    Skips malformed JSON lines silently.
    """
    try:
        from AuditLogger import AuditLogger
        log_path = AuditLogger(folder_path).log_path
    except Exception:
        return {}

    if not os.path.isfile(log_path):
        return {}

    processed = {}
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip malformed lines — do NOT crash
                if entry.get("rule") == rule_title and entry.get("status") in ("success", "intent"):
                    # .FolderActions.py writes "file"; older entries may use "item"
                    filename = os.path.basename(entry.get("file") or entry.get("item", ""))
                    ts = entry.get("ts", "")
                    if filename:
                        processed[filename] = ts  # last-write wins
    except OSError:
        return {}
    return processed


def scan_folder_for_rule(folder_path: str, rule_criteria: list, *, _fa_mod=None) -> list:
    """
    Return absolute paths of files in folder_path that match rule_criteria.
    Uses match_criteria() from .FolderActions.py — criteria-only, no side effects.
    Passes entry.name to match_criteria (filename-only, never a full path).
    Pass _fa_mod to reuse an already-loaded module and avoid double exec_module().
    """
    if not os.path.isdir(folder_path):
        return []

    fa_mod = _fa_mod if _fa_mod is not None else _load_folder_actions_module()
    if fa_mod is None or not hasattr(fa_mod, "match_criteria"):
        return []

    match_criteria = fa_mod.match_criteria
    matched = []
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file():
                if all(match_criteria(entry.name, c) for c in rule_criteria):
                    matched.append(entry.path)
    except OSError:
        return []
    return matched


# ─── YAML PARSING ─────────────────────────────────────────────────────────────

def parse_yaml_file(yaml_path):
    """Parse .FolderActions.yaml → (rules list, aiRules dict or None)."""
    try:
        with open(yaml_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception:
        return [], None

    if not isinstance(config, dict):
        return [], None

    rules = []
    for i, r in enumerate(config.get("Rules", [])):
        title = r.get("Title", f"Rule {i + 1}")
        actions = _normalize_actions(r.get("Actions", []))
        dest = ""
        for action in actions:
            if "MoveToFolder" in action:
                dest = action["MoveToFolder"]
                break

        mode, parsed_criteria, groups = parse_criteria(r.get("Criteria", []))
        rules.append({
            "id": f"r{i}",
            "title": title,
            "mode": mode,
            "criteria": parsed_criteria,
            "groups": groups,
            "dest": dest,
            "actions": actions,
            "modified": False,
            "isNew": False,
        })

    ai_rules = None
    ai_cfg = config.get("AiRules")
    if isinstance(ai_cfg, dict) and ai_cfg.get("Model"):
        ai_rules = {
            "model": ai_cfg.get("Model", "llama3.2"),
            "confidenceThreshold": ai_cfg.get("ConfidenceThreshold", 0.8),
            "timeoutSeconds": ai_cfg.get("TimeoutSeconds", 60),
            "rules": [],
        }
        for ar in ai_cfg.get("Rules", []):
            ar_dest = ""
            for a in ar.get("Actions", []):
                if "MoveToFolder" in a:
                    ar_dest = a["MoveToFolder"]
                    break
            ai_rules["rules"].append({
                "title": ar.get("Title", ""),
                "description": ar.get("Description", ""),
                "dest": ar_dest,
            })

    return rules, ai_rules


def parse_criteria(criteria_list):
    """Convert YAML criteria list → (mode, criteria, groups)."""
    if not criteria_list:
        return "simple", [], []

    if len(criteria_list) == 1:
        item = criteria_list[0]
        if not isinstance(item, dict):
            return "simple", [], []

        if "AllCriteria" in item:
            parsed = _parse_simple_list(item["AllCriteria"])
            return "and", parsed, []

        if "AnyCriteria" in item:
            sub = item["AnyCriteria"] or []
            # All items are AllCriteria groups → groups mode
            if sub and all(isinstance(s, dict) and "AllCriteria" in s for s in sub):
                groups = [_parse_simple_list(s["AllCriteria"]) for s in sub]
                return "groups", [], groups
            # Some AllCriteria + some simple → mixed groups
            if sub and any(isinstance(s, dict) and "AllCriteria" in s for s in sub):
                groups = []
                for s in sub:
                    if isinstance(s, dict) and "AllCriteria" in s:
                        groups.append(_parse_simple_list(s["AllCriteria"]))
                    else:
                        c = _crit_item(s)
                        if c:
                            groups.append([c])
                return "groups", [], groups
            # Simple items → or mode
            return "or", _parse_simple_list(sub), []

        # Direct simple criterion (e.g., {FileExtension: xlsx})
        c = _crit_item(item)
        return "simple", ([c] if c else []), []

    # Multiple items → direct AND list
    parsed = _parse_simple_list(criteria_list)
    return ("simple" if len(parsed) == 1 else "and"), parsed, []


def _parse_simple_list(items):
    return [c for c in (_crit_item(i) for i in (items or [])) if c]


def _crit_item(item):
    if not isinstance(item, dict):
        return None
    if "FileExtension" in item:
        return {"type": "ext", "value": str(item["FileExtension"])}
    if "FileNameContains" in item:
        return {"type": "name", "value": str(item["FileNameContains"])}
    return None


def _normalize_actions(actions):
    normalized = []
    for action in actions or []:
        if isinstance(action, dict):
            normalized.append(dict(action))
    return normalized


# ─── YAML GENERATION (SAVE) ───────────────────────────────────────────────────

def rules_to_yaml(rules, ai_rules):
    """Convert dashboard rules + aiRules back to YAML text."""
    config = {"Rules": []}

    for r in rules:
        actions = _serialize_rule_actions(r)
        config["Rules"].append({
            "Title": r["title"],
            "Criteria": _build_criteria_yaml(r),
            "Actions": actions,
        })

    if ai_rules and ai_rules.get("rules"):
        ai_cfg = {
            "Model": ai_rules.get("model", "llama3.2"),
            "ConfidenceThreshold": ai_rules.get("confidenceThreshold", 0.8),
        }
        ts = ai_rules.get("timeoutSeconds", 60)
        if ts != 60:
            ai_cfg["TimeoutSeconds"] = ts
        ai_cfg["Rules"] = [
            {
                "Title": ar["title"],
                "Description": ar["description"],
                "Actions": [{"MoveToFolder": ar["dest"]}],
            }
            for ar in ai_rules.get("rules", [])
        ]
        config["AiRules"] = ai_cfg

    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _serialize_rule_actions(rule):
    """Preserve existing action order while applying edited MoveToFolder values."""
    actions = _normalize_actions(rule.get("actions", []))
    if not actions:
        return [{"MoveToFolder": rule.get("dest", "")}]

    serialized = []
    move_written = False
    for action in actions:
        if "MoveToFolder" in action:
            serialized.append({"MoveToFolder": rule.get("dest", action["MoveToFolder"])})
            move_written = True
        else:
            serialized.append(dict(action))

    if rule.get("dest") and not move_written:
        serialized.append({"MoveToFolder": rule["dest"]})
    return serialized


def _build_criteria_yaml(rule):
    mode = rule.get("mode", "simple")
    criteria = rule.get("criteria", [])
    groups = rule.get("groups", [])

    def c2y(c):
        return {"FileExtension": c["value"]} if c["type"] == "ext" else {"FileNameContains": c["value"]}

    if mode == "simple":
        return [c2y(c) for c in criteria]
    if mode == "and":
        if len(criteria) == 1:
            return [c2y(criteria[0])]
        return [{"AllCriteria": [c2y(c) for c in criteria]}]
    if mode == "or":
        return [{"AnyCriteria": [c2y(c) for c in criteria]}]
    if mode == "groups":
        items = []
        for g in groups:
            items.append(c2y(g[0]) if len(g) == 1 else {"AllCriteria": [c2y(c) for c in g]})
        return [{"AnyCriteria": items}]
    return [c2y(c) for c in criteria]


# ─── HTTP SERVER ───────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    html_path = os.path.join(SCRIPT_DIR, "folder-actions-dashboard.html")

    def log_message(self, format, *args):
        pass  # suppress request logging

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/api/data":
            self._serve_data()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/save":
            self._handle_save()
        elif path == "/api/retroactive":
            self._handle_retroactive()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._add_acao_if_local()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _add_acao_if_local(self):
        """Only allow CORS from localhost origins (blocks cross-site CSRF)."""
        origin = self.headers.get("Origin", "")
        if not origin or "localhost" in origin or "127.0.0.1" in origin:
            self.send_header("Access-Control-Allow-Origin", origin or "*")

    def _serve_html(self):
        try:
            with open(DashboardHandler.html_path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            msg = b"Dashboard HTML not found."
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_data(self):
        logs = load_logs()
        sources = find_sources(logs)
        self._send_json({"logs": logs, "sources": sources})

    def _handle_save(self):
        try:
            # Body size limit (prevents memory exhaustion)
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                self._send_json({"error": "Request body too large"}, 413)
                return
            body = self.rfile.read(length)
            req = json.loads(body)

            yaml_path = req.get("yamlPath", "")
            if not yaml_path:
                self._send_json({"error": "yamlPath required"}, 400)
                return

            # Validate against server-known sources (prevents path traversal + CSRF write)
            real_path = os.path.realpath(os.path.expanduser(yaml_path))
            logs = load_logs()
            valid_paths = {
                os.path.realpath(s["yamlPath"]) for s in find_sources(logs)
            }
            if real_path not in valid_paths:
                self._send_json({"error": "Path not in known watched folders"}, 403)
                return

            content = rules_to_yaml(req.get("rules", []), req.get("aiRules"))

            # Atomic write: write to .tmp then replace (prevents partial-write corruption)
            dir_name = os.path.dirname(real_path)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, real_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            self._send_json({"ok": True, "path": yaml_path})
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid request body"}, 400)
        except Exception:
            self._send_json({"error": "Internal server error"}, 500)

    def _handle_retroactive(self):
        """
        POST /api/retroactive
        Body: {"source_index": N, "rule_index": M, "action": "preview"|"run"}

        preview: scan folder, check audit log, return file status list (no mutations).
        run:     same as preview but execute the rule on unprocessed files.

        File status values: "unprocessed", "processed", "skipped" (file missing on disk).
        """
        try:
            # Body size limit (matches _handle_save)
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                self._send_json({"error": "Request body too large"}, 413)
                return
            body = self.rfile.read(length)
            req = json.loads(body)

            source_index = req.get("source_index")
            rule_index = req.get("rule_index")
            action = req.get("action", "")
            # folder_path from client lets us validate the positional index is stable
            client_folder = req.get("folder_path", "")

            if action not in ("preview", "run"):
                self._send_json({"error": "action must be 'preview' or 'run'"}, 400)
                return

            # Resolve source and rule from live data
            logs = load_logs()
            sources = find_sources(logs)

            if not isinstance(source_index, int) or isinstance(source_index, bool) or not (0 <= source_index < len(sources)):
                self._send_json({"error": "source_index out of range"}, 400)
                return

            source = sources[source_index]

            # Validate the positional index points to the expected folder (guard against
            # concurrent source list shifts between render and run)
            if client_folder and os.path.abspath(client_folder) != os.path.abspath(source["folder"]):
                self._send_json({"error": "source_index no longer matches folder — refresh the page"}, 409)
                return

            rules = source.get("rules", [])

            if not isinstance(rule_index, int) or isinstance(rule_index, bool) or not (0 <= rule_index < len(rules)):
                self._send_json({"error": "rule_index out of range"}, 400)
                return

            rule = rules[rule_index]
            folder_path = source["folder"]
            rule_title = rule["title"]

            # Reconstruct raw YAML criteria list for scan_folder_for_rule
            raw_criteria = _build_criteria_yaml(rule)

            # Load module once for the whole request (avoids double exec_module on run)
            fa_mod = _load_folder_actions_module()
            matching_files = scan_folder_for_rule(folder_path, raw_criteria, _fa_mod=fa_mod)

            # File count limits to prevent blocking the single-threaded server
            max_files = MAX_RETROACTIVE_RUN_FILES if action == "run" else MAX_RETROACTIVE_PREVIEW_FILES
            if len(matching_files) > max_files:
                self._send_json({
                    "error": f"Too many files ({len(matching_files)}), max {max_files} for action '{action}'"
                }, 400)
                return

            processed = get_processed_files(folder_path, rule_title)

            result_files = []
            if action == "preview":
                for filepath in sorted(matching_files):
                    filename = os.path.basename(filepath)
                    if not os.path.exists(filepath):
                        status = "skipped"
                        last_processed = ""
                    elif filename in processed:
                        status = "processed"
                        last_processed = processed[filename]
                    else:
                        status = "unprocessed"
                        last_processed = ""
                    result_files.append({
                        "name": filename,
                        "status": status,
                        "last_processed": last_processed,
                    })
            else:  # run
                apply_rule = getattr(fa_mod, "apply_rule_by_yaml_config", None) if fa_mod else None

                for filepath in sorted(matching_files):
                    filename = os.path.basename(filepath)
                    if not os.path.exists(filepath):
                        result_files.append({"name": filename, "status": "skipped", "last_processed": ""})
                        continue
                    if filename in processed:
                        result_files.append({
                            "name": filename,
                            "status": "processed",
                            "last_processed": processed[filename],
                        })
                        continue
                    if apply_rule is None:
                        result_files.append({"name": filename, "status": "error", "last_processed": ""})
                        continue

                    # Execute rule on this file (item must be filename-only)
                    try:
                        apply_rule(folder_path, filename, config={"Rules": [
                            {
                                "Title": rule_title,
                                "Criteria": raw_criteria,
                                "Actions": rule.get("actions", []),
                            }
                        ]})
                        result_files.append({"name": filename, "status": "run", "last_processed": ""})
                    except Exception:
                        result_files.append({"name": filename, "status": "error", "last_processed": ""})

            unprocessed_count = sum(1 for f in result_files if f["status"] == "unprocessed")
            self._send_json({
                "files": result_files,
                "total": len(result_files),
                "unprocessed": unprocessed_count,
            })

        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "Invalid request body"}, 400)
        except Exception:
            self._send_json({"error": "Internal server error"}, 500)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_acao_if_local()
        self.end_headers()
        self.wfile.write(body)


def _find_free_port(start=DEFAULT_PORT):
    for port in range(start, start + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found starting from {start}")


def main():
    parser = argparse.ArgumentParser(description="Folder Actions Dashboard")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    port = args.port if args.port else _find_free_port()

    # Guard against TOCTOU race: _find_free_port releases the socket before HTTPServer binds
    try:
        server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError:
        port = _find_free_port(port + 1)
        server = HTTPServer(("127.0.0.1", port), DashboardHandler)

    url = f"http://localhost:{port}"
    print(f"Folder Actions Dashboard  →  {url}")
    print(f"Log directory: {LOG_DIR}")
    print("Press Ctrl+C to stop\n")

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
