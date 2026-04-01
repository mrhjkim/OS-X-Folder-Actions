OS-X-Folder-Actions
===================
Found these here: http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style.

## Folder Actions, UNIX style

Mac OS X has a nice feature called Folder Actions. Basically, this feature lets you attach an AppleScript script to a folder, and have that script run whenever items are added or removed from the folder. Have a look here for a simple example.

How would you write that script in python? Here’s my simple, general-purpose solution.

There are four main components:

1. **Send Events To Shell Script.applescript** — attaches to a folder and forwards Folder Action events (open, close, add, remove) to the dispatcher.

2. **FolderActionsDispatcher.sh / FolderActionsDispatcher.py** — receives events from AppleScript and dispatches them to the rule engine.

3. **.FolderActions.py** — the 3-stage rule engine:
   - **Stage 1 (YAML rules):** fast, deterministic matching by filename/extension
   - **Stage 2 (AI rules):** local LLM via [Ollama](https://ollama.ai) classifies files by content when YAML rules don't match
   - **Stage 3 (Fallthrough):** explicit no-match log entry

4. **folder-actions log** — CLI to query the JSONL audit log (`--file`, `--rule`, `--since`, `--watch`).

All you have to do is write a `.FolderActions.yaml` config file and place it in the folder you want to watch.

## Installation

Here’s an example. Let’s say that we want to copy every file placed in ~/Downloads to some directory, and do it automatically. Here’s what we will do:

1. One-time setup:

   1. Clone this repo.
   2. Run **`./install.sh`** — creates a virtualenv, installs dependencies, copies scripts to `~/.local/bin`, and sets up the `folder-actions` CLI.
   3. Open a new terminal (or run `source ~/.zshrc`) so `~/.local/bin` is on your PATH.
   4. Attach the AppleScript: right-click your target folder in Finder → **Folder Actions Setup…** → select **Send Events To Shell Script.applescript**.

2. Create `~/Downloads/.FolderActions.yaml`:

```yaml
Rules:
  - Title: "PDF documents"
    Criteria:
      - FileExtension: pdf
    Actions:
      - MoveToFolder: ~/Documents/PDFs/

  - Title: "Weekly reports"
    Criteria:
      - AllCriteria:
          - FileExtension: xlsx
          - FileNameContains: "weekly"
    Actions:
      - MoveToFolder: ~/Documents/Reports/

# Optional: AI rules (requires Ollama — https://ollama.ai)
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  TimeoutSeconds: 60      # optional, default 60 — increase for large models
  Rules:
    - Title: "Tax documents"
      Description: "Tax receipts, invoices, or financial records"
      Actions:
        - MoveToFolder: ~/Documents/Finance/Tax/

# Audit log is on by default (~/.folder-actions-log/)
# To disable: Audit: {Enabled: false}
```

3. Drop a file into `~/Downloads` and watch it move.

4. View the audit log:

```bash
folder-actions log              # last 20 entries
folder-actions log --watch      # live tail
folder-actions log --file invoice --since 2026-01-01
```
