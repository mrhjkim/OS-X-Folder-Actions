OS-X-Folder-Actions
===================
Found these here: http://j4zzcat.wordpress.com/2010/01/06/folder-actions-unix-style.

[한국어](README.ko.md)

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

4. **folder-actions log / dashboard** — two ways to review what happened:
   - `folder-actions log` — CLI to query the JSONL audit log (`--file`, `--rule`, `--since`, `--watch`)
   - `folder-actions dashboard` — interactive web dashboard: browse logs, spot unmatched files, edit rules, save changes back to `.FolderActions.yaml`, and retroactively apply rules to files already in the watched folder

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

5. Or open the visual dashboard to browse logs and edit rules:

```bash
folder-actions dashboard        # opens http://localhost:7373 in your browser
folder-actions dashboard --port 8080
```

The dashboard reads your live audit log, shows which files matched (or didn't), lets you edit rules directly, and saves changes back to `.FolderActions.yaml` with one click. It also supports **retroactive apply** — if you create a new rule or change an existing one, expand the rule card and click "Preview" to see which files haven't been processed yet, then "Run" to apply the rule to them.

## AiAgent actions

You can add `AiAgent:` under normal `Rules` when you want a matching file to trigger
an AI CLI command instead of just moving the file.

```yaml
Rules:
  - Title: "Summarize PDFs"
    Criteria:
      - FileExtension: pdf
    Actions:
      - AiAgent:
          Model: claude
          PromptFile: ~/.config/folder-actions/summarize-pdf.txt
          AllowDangerousPermissions: true   # opt-in: lets Claude act without prompts
      - MoveToFolder: ~/Documents/PDFs/
```

Supported variables in the prompt template:
- `{filepath}` full file path
- `{filename}` file name without extension
- `{basename}` file name with extension
- `{folder}` containing folder
- `{ext}` extension without the dot

Notes:
- Verified providers in this release: `claude`, `codex`
- `gemini` is reserved in the config surface but returns an explicit "not yet verified" error
- Actions run sequentially, so `MoveToFolder` followed by `AiAgent` runs the AI command on the moved path
- The dashboard preserves `AiAgent` actions when saving YAML files
- AiAgent actions are synchronous — the handler blocks until the AI command finishes (up to `TimeoutSeconds`, default 120s). For folders with high drop frequency, use a lower `TimeoutSeconds` or avoid chaining multiple AiAgent actions.

**Security note:** By default, `AllowDangerousPermissions` is `false` — the AI agent will prompt before taking destructive actions. Set it to `true` only for fully automated workflows where you trust both the prompt template and the files being dropped. File names and paths are embedded in the prompt; rename them to avoid unintended instructions.
