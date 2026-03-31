import os
import json
import fcntl
import hashlib
import logging
from datetime import datetime, timezone


class AuditLogger:
    """
    Append-only JSONL audit log for Folder Actions events.

    Log directory : ~/.folder-actions-log/
    Log filename  : {folder_leaf}-{md5(abs_path)[:6]}.jsonl

    API:
        write(entry)               - single-step append (Stage 1 / Stage 3)
        write_intent(entry) -> id  - append with status="intent" (Stage 2 before action)
        update(entry_id, **kwargs) - rewrite matching line (Stage 2 after action)
    """

    MAX_FIELD_LEN = 1024  # cap string fields to stay under atomic write limits

    def __init__(self, folder_path: str, log_dir: str = None):
        """
        folder_path : absolute path of the watched folder (used for log filename)
        log_dir     : override default ~/.folder-actions-log/
        """
        self.folder_path = os.path.abspath(folder_path)
        self.log_dir = os.path.expanduser(log_dir or "~/.folder-actions-log")
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = self._build_log_path()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, entry: dict) -> None:
        """Single-step atomic append. Used by Stage 1 and Stage 3."""
        line = json.dumps(self._cap_fields(entry), ensure_ascii=False)
        with open(self.log_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)

    def write_intent(self, entry: dict) -> str:
        """
        Append entry with status='intent'. Returns entry_id for later update().
        Used by Stage 2 before executing the file action.
        """
        entry_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        intent_entry = dict(entry)
        intent_entry["id"] = entry_id
        intent_entry["status"] = "intent"
        self.write(intent_entry)
        return entry_id

    def update(self, entry_id: str, **kwargs) -> None:
        """
        Rewrite the line matching entry_id with updated fields.
        Uses fcntl.LOCK_EX for inter-process safety.
        If entry_id is not found, logs a warning and returns (no exception).
        """
        if not os.path.exists(self.log_path):
            logging.warning(f"AuditLogger.update: log file missing: {self.log_path}")
            return

        try:
            with open(self.log_path, "r+", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                lines = f.readlines()
                found = False
                for i, line in enumerate(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("id") == entry_id:
                        entry.update(kwargs)
                        lines[i] = json.dumps(self._cap_fields(entry), ensure_ascii=False) + "\n"
                        found = True
                        break
                if found:
                    f.seek(0)
                    f.writelines(lines)
                    f.truncate()
                else:
                    logging.warning(
                        f"AuditLogger.update: entry_id {entry_id!r} not found in {self.log_path}"
                    )
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError as e:
            logging.warning(f"AuditLogger.update: OS error: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_log_path(self) -> str:
        leaf = os.path.basename(self.folder_path) or "root"
        h = hashlib.md5(self.folder_path.encode("utf-8")).hexdigest()[:6]
        filename = f"{leaf}-{h}.jsonl"
        return os.path.join(self.log_dir, filename)

    def _cap_fields(self, entry: dict) -> dict:
        """Cap all string values to MAX_FIELD_LEN characters."""
        result = {}
        for k, v in entry.items():
            if isinstance(v, str) and len(v) > self.MAX_FIELD_LEN:
                result[k] = v[: self.MAX_FIELD_LEN]
            else:
                result[k] = v
        return result
