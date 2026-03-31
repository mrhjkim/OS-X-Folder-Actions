#!/usr/bin/env python3
"""
folder-actions log — CLI viewer for the Folder Actions audit log.

Usage:
    folder-actions log                       # last 20 entries, all folders
    folder-actions log --file invoice        # filter by filename substring
    folder-actions log --rule "Tax"          # filter by rule name substring
    folder-actions log --since 2026-03-01   # ISO date, local midnight → UTC
    folder-actions log --watch              # live tail (poll every 1s)
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone


LOG_DIR = os.path.expanduser("~/.folder-actions-log")
DEFAULT_LIMIT = 20


def main():
    parser = argparse.ArgumentParser(
        prog="folder-actions log",
        description="View Folder Actions audit history",
    )
    parser.add_argument("--file", metavar="SUBSTR", help="Filter by filename substring")
    parser.add_argument("--rule", metavar="SUBSTR", help="Filter by rule name substring")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="Show entries from this date (local midnight)")
    parser.add_argument("--watch", action="store_true", help="Live tail — poll for new entries")
    args = parser.parse_args()

    since_dt = None
    if args.since:
        try:
            local_midnight = datetime.strptime(args.since, "%Y-%m-%d")
            since_dt = local_midnight.astimezone(timezone.utc)
        except ValueError:
            print(f"Error: --since must be YYYY-MM-DD, got: {args.since}", file=sys.stderr)
            sys.exit(1)

    if args.watch:
        _watch(args.file, args.rule, since_dt)
    else:
        entries = _load_all(args.file, args.rule, since_dt)
        if not entries:
            print("No entries yet.")
            return
        entries = entries[-DEFAULT_LIMIT:]
        _print_entries(entries)


def _load_all(file_filter, rule_filter, since_dt):
    """Load and merge all JSONL files, sorted by timestamp."""
    if not os.path.isdir(LOG_DIR):
        return []

    files = glob.glob(os.path.join(LOG_DIR, "*.jsonl"))
    entries = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # skip intent entries (in-progress Stage 2)
                    if entry.get("status") == "intent":
                        continue
                    if _matches(entry, file_filter, rule_filter, since_dt):
                        entries.append(entry)
        except OSError:
            continue

    entries.sort(key=lambda e: e.get("ts", ""))
    return entries


def _matches(entry, file_filter, rule_filter, since_dt):
    if file_filter and file_filter.lower() not in (entry.get("file") or "").lower():
        return False
    if rule_filter and rule_filter.lower() not in (entry.get("rule") or "").lower():
        return False
    if since_dt:
        ts_str = entry.get("ts", "")
        if not ts_str:
            return False
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts < since_dt:
                return False
        except ValueError:
            return False
    return True


def _print_entries(entries):
    print(f"Recent activity ({len(entries)} entries, all folders):\n")
    for entry in entries:
        _print_entry(entry)


def _print_entry(entry):
    ts = entry.get("ts", "")
    time_str = ts[11:16] if len(ts) >= 16 else ts  # HH:MM from ISO
    filename = entry.get("file", "(unknown)")
    source = _shorten(entry.get("source", ""))
    stage = entry.get("stage", "")
    rule = entry.get("rule")
    destination = _shorten(entry.get("destination") or "")
    confidence = entry.get("confidence")
    action_error = entry.get("error")

    if stage == "fallthrough":
        dest_str = "no rule matched"
        if action_error:
            dest_str += f" (error: {action_error[:80]})"
    elif destination:
        dest_str = destination
    else:
        dest_str = "(no destination)"

    print(f"{time_str}  {filename:<35} {source} → {dest_str}")

    if rule:
        stage_label = stage.upper() if stage else "?"
        if confidence is not None:
            pct = int(float(confidence) * 100)
            print(f"       rule: \"{rule}\" ({stage_label}, {pct}%)")
        else:
            print(f"       rule: \"{rule}\" ({stage_label})")
    elif action_error and stage != "fallthrough":
        print(f"       error: {action_error[:80]}")
    print()


def _shorten(path: str) -> str:
    """Replace $HOME with ~ for display."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _watch(file_filter, rule_filter, since_dt):
    """Poll for new entries every 1 second. Re-globs to catch new log files."""
    print(f"[watching {LOG_DIR} for new entries — Ctrl+C to stop]\n")

    seen = set()
    # Seed with current entries (don't print them)
    for entry in _load_all(file_filter, rule_filter, since_dt):
        key = _entry_key(entry)
        seen.add(key)

    try:
        while True:
            current = _load_all(file_filter, rule_filter, since_dt)
            for entry in current:
                key = _entry_key(entry)
                if key not in seen:
                    seen.add(key)
                    _print_entry(entry)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[stopped]")


def _entry_key(entry):
    """Unique key for deduplication in --watch mode."""
    return (entry.get("id"), entry.get("ts"), entry.get("file"))


if __name__ == "__main__":
    main()
