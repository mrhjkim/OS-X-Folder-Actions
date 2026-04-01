import os
import shutil
import logging
import subprocess
import unicodedata
import difflib
import yaml

# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------
LOG_FILE = os.path.expanduser("~/Desktop/FolderActions.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),
        logging.StreamHandler(),
    ],
)

# ------------------------------------------------------------------
# Optional imports (non-fatal if not yet installed)
# ------------------------------------------------------------------
try:
    from AuditLogger import AuditLogger
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False
    logging.warning("AuditLogger not found — audit logging disabled")

try:
    import ContentExtractor
    _EXTRACTOR_AVAILABLE = True
except ImportError:
    _EXTRACTOR_AVAILABLE = False
    logging.warning("ContentExtractor not found — AI content extraction disabled")

try:
    import AIProvider
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False
    logging.warning("AIProvider not found — AI rules disabled")

# ------------------------------------------------------------------
# Known top-level YAML keys (for typo suggestions)
# ------------------------------------------------------------------
_KNOWN_YAML_KEYS = ["Rules", "AiRules", "Audit"]

CONFIG_FILE = ".FolderActions.conf"


# ------------------------------------------------------------------
# Notification / logging helper
# ------------------------------------------------------------------

def log(message, rule_title=None, stage=None):
    """Log message to file + stderr. Fire macOS notification only on rule match."""
    logging.info(message)
    if rule_title is not None:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "Folder Actions"'],
            capture_output=True,
        )


# ------------------------------------------------------------------
# Event callbacks
# ------------------------------------------------------------------

def folder_opened(folder):
    log(f"Folder {folder} opened")


def folder_closed(folder):
    log(f"Folder {folder} closed")


def item_added_to_folder(folder, item):
    log(f"Item {item} added to folder {folder}")
    file_path = os.path.join(folder, item)

    config_path = os.path.join(folder, ".FolderActions.yaml")
    config = _load_yaml_config(config_path)

    audit_cfg = config.get("Audit", {}) if config else {}
    audit_enabled = audit_cfg.get("Enabled", True) if audit_cfg is not None else True
    audit_log_dir = audit_cfg.get("Path") if audit_cfg else None

    audit = None
    if audit_enabled and _AUDIT_AVAILABLE:
        try:
            audit = AuditLogger(folder, log_dir=audit_log_dir)
        except Exception as e:
            logging.warning(f"AuditLogger init failed: {e}")

    # ------------------------------------------------------------------
    # Stage 1: YAML rules
    # ------------------------------------------------------------------
    matched, rule_title, destination, action_error = apply_rule_by_yaml_config(
        folder, item, config
    )

    if matched:
        status = "error" if action_error else "success"
        msg = (
            f"{item} → {destination} (YAML: {rule_title})"
            if not action_error
            else f"{item} → YAML rule '{rule_title}' failed: {action_error}"
        )
        log(msg, rule_title=rule_title, stage="YAML")
        if audit:
            audit.write({
                "ts": _utcnow(),
                "file": item,
                "source": folder,
                "event": "added",
                "stage": "yaml",
                "rule": rule_title,
                "action": "move" if destination else "run_script",
                "destination": destination,
                "status": status,
                "error": action_error,
            })
        return

    # ------------------------------------------------------------------
    # Stage 2: AI rules
    # ------------------------------------------------------------------
    ai_cfg = config.get("AiRules") if config else None
    if ai_cfg and _AI_AVAILABLE and _EXTRACTOR_AVAILABLE:
        ai_rules = ai_cfg.get("Rules", [])
        model = ai_cfg.get("Model")
        threshold = float(ai_cfg.get("ConfidenceThreshold", 0.8))

        if ai_rules and model:
            snippet = ContentExtractor.extract(file_path)
            result = AIProvider.query(snippet, ai_rules, model)

            if not result["error"] and result["matched_rule"] and result["confidence"] >= threshold:
                dest_dir = result["destination"]
                if dest_dir:
                    entry_id = None
                    if audit:
                        entry_id = audit.write_intent({
                            "ts": _utcnow(),
                            "file": item,
                            "source": folder,
                            "event": "added",
                            "stage": "ai",
                            "rule": result["matched_rule"],
                            "action": "move",
                            "destination": dest_dir,
                            "confidence": result["confidence"],
                            "reason": result["reason"],
                        })

                    move_error = None
                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                        dest_path = os.path.join(dest_dir, item)
                        shutil.move(file_path, dest_path)
                    except OSError as e:
                        move_error = str(e)
                        logging.error(f"AI Stage move failed: {e}")

                    status = "error" if move_error else "success"
                    if audit and entry_id:
                        audit.update(entry_id, status=status, error=move_error)

                    if not move_error:
                        pct = int(result["confidence"] * 100)
                        msg = f"{item} → {result['matched_rule']} (AI: {pct}% confident)"
                        log(msg, rule_title=result["matched_rule"], stage="AI")
                    return

    # ------------------------------------------------------------------
    # Stage 3: Fallthrough
    # ------------------------------------------------------------------
    log(f"No matching rule for {item} in {folder}")
    if audit:
        audit.write({
            "ts": _utcnow(),
            "file": item,
            "source": folder,
            "event": "added",
            "stage": "fallthrough",
            "rule": None,
            "action": None,
            "status": None,
            "error": None,
        })


def item_removed_from_folder(folder, item):
    log(f"Item {item} removed from folder {folder}")


# ------------------------------------------------------------------
# Stage 1: YAML rule engine
# ------------------------------------------------------------------

def _load_yaml_config(config_path: str):
    """Load and validate .FolderActions.yaml. Returns None on missing/parse error."""
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        log(f"Error parsing YAML config: {exc}")
        return None

    if not isinstance(config, dict):
        return None

    # Typo suggestions for unknown top-level keys
    for key in config:
        if key not in _KNOWN_YAML_KEYS:
            suggestions = difflib.get_close_matches(key, _KNOWN_YAML_KEYS, n=1, cutoff=0.6)
            hint = f" — did you mean '{suggestions[0]}'?" if suggestions else ""
            logging.warning(f"Unknown YAML key '{key}'{hint}")

    # Validate AiRules actions (only MoveToFolder allowed)
    ai_cfg = config.get("AiRules", {})
    if ai_cfg:
        if not ai_cfg.get("Model"):
            logging.error("AiRules.Model is required — skipping AI rules section")
            config.pop("AiRules", None)
        else:
            for rule in ai_cfg.get("Rules", []):
                for action in rule.get("Actions", []):
                    if "RunShellScript" in action:
                        logging.error(
                            f"AiRules.Rules['{rule.get('Title')}'].Actions contains RunShellScript — "
                            "only MoveToFolder is allowed under AiRules in v1. RunShellScript will be ignored."
                        )

    return config


def apply_rule_by_yaml_config(folder: str, item: str, config=None):
    """
    Evaluate YAML rules against item.

    Returns 4-tuple:
        (matched: bool, rule_title: str|None, destination: str|None, action_error: str|None)

    action_error is set when a rule matched but the action (move/script) failed.
    """
    item_path = os.path.join(folder, item)

    if not os.path.isfile(item_path) and not os.path.isdir(item_path):
        log(f"File not found: {item_path}")
        return (False, None, None, None)

    if config is None:
        config_path = os.path.join(folder, ".FolderActions.yaml")
        config = _load_yaml_config(config_path)

    if not config:
        return (False, None, None, None)

    item_nfc = unicodedata.normalize("NFC", item)

    for rule in config.get("Rules", []):
        criteria = rule.get("Criteria", [])
        actions = rule.get("Actions", [])
        rule_title = rule.get("Title", "(unnamed)")

        if not criteria or not actions:
            continue

        if not all(match_criteria(item_nfc, criterion) for criterion in criteria):
            continue

        # Rule matched — execute actions
        action_succeeded = False
        action_error = None
        last_destination = None

        for action in actions:
            if "MoveToFolder" in action:
                target_folder = os.path.expanduser(action["MoveToFolder"])
                last_destination = target_folder
                try:
                    if not os.path.isdir(target_folder):
                        log(f"Target folder not found: {target_folder}. Creating folder.")
                        os.makedirs(target_folder, exist_ok=True)
                    target_path = os.path.join(target_folder, item)
                    shutil.move(item_path, target_path)
                    action_succeeded = True
                    last_destination = target_path
                    log(f"Moved {item_path} to {target_path}")
                except OSError as e:
                    action_error = str(e)
                    logging.error(f"Move failed: {e}")

            elif "RunShellScript" in action:
                script = action["RunShellScript"]
                try:
                    env = os.environ.copy()
                    env["FILENAME"] = item_path
                    result = subprocess.run(
                        script, shell=True, check=True,
                        capture_output=True, env=env, cwd=folder,
                    )
                    action_succeeded = True
                    log(f"Executed: {script} with FILENAME={item_path}")
                except subprocess.CalledProcessError as e:
                    action_error = str(e)
                    logging.error(
                        f"Shell command failed: {script}: {e}\n"
                        f"stdout: {e.stdout.decode()}\nstderr: {e.stderr.decode()}"
                    )

        return (True, rule_title, last_destination, action_error)

    log(f"No matching rule for {item} in {folder}")
    return (False, None, None, None)


def match_criteria(item: str, criterion: dict) -> bool:
    """
    Evaluate a single criterion dict against the item filename.
    Unknown criterion types fail-closed (return False).
    """
    item_name, item_extension = os.path.splitext(item)
    item_extension = item_extension.lstrip(".")

    for key, value in criterion.items():
        if key == "AllCriteria":
            if not all(match_criteria(item, sub) for sub in value):
                return False
        elif key == "AnyCriteria":
            if not any(match_criteria(item, sub) for sub in value):
                return False
        elif key == "FileExtension":
            if item_extension != value:
                return False
        elif key == "FileNameContains":
            if value not in item_name:
                return False
        else:
            # Unknown criterion key: fail-closed (don't match)
            logging.warning(f"match_criteria: unknown key '{key}' — treating as no-match")
            return False

    return True


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------

def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
