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

# Optional: AI rules — classify by content when YAML rules don't match.
# Default backend is local Ollama (https://ollama.ai); nothing leaves your Mac.
AiRules:
  Model: llama3.2
  ConfidenceThreshold: 0.8
  TimeoutSeconds: 60      # optional, default 60s (both backends)
  Rules:
    - Title: "Tax documents"
      Description: "Tax receipts, invoices, or financial records"
      Actions:
        - MoveToFolder: ~/Documents/Finance/Tax/

# Audit log is on by default (~/.folder-actions-log/)
# To disable: Audit: {Enabled: false}
```

### AI backends: Ollama (local) or Gemini (API key)

`AiRules` classifies files by content. The default backend is **Ollama**, running
locally — file contents never leave your Mac. To use Google's **Gemini** API instead,
add a `Provider` and point at a key file:

```yaml
AiRules:
  Provider: gemini                                   # omit → ollama (local, default)
  Model: gemini-3.5-flash                            # verify current IDs at ai.google.dev
  ApiKeyFile: ~/.config/folder-actions/gemini.key
  ConfidenceThreshold: 0.8
  Rules:
    - Title: "청구서"
      Description: "인보이스, 영수증, 결제 내역"
      Actions:
        - MoveToFolder: ~/Documents/Invoices/
```

The key lives in a file outside the repo, never in `.FolderActions.yaml` (which is
tracked by git):

```bash
mkdir -p ~/.config/folder-actions
printf '%s' 'YOUR_GEMINI_API_KEY' > ~/.config/folder-actions/gemini.key
chmod 600 ~/.config/folder-actions/gemini.key
```

Key resolution order: the `GEMINI_API_KEY` environment variable first (handy for a
terminal), then `ApiKeyFile`. macOS Folder Actions runs from a GUI daemon with almost
no environment, so **the key file is what makes it work on a real file drop** — an env
var alone will pass every test and then silently fail when you actually drop a file.

> ⚠️ **`Provider: gemini` sends file contents off your machine.** Up to the first 4096
> characters of every classified file (PDF, docx, xlsx, txt) are uploaded to Google's
> API. Watched folders usually hold invoices, contracts, and payslips. If that matters
> for a folder, keep it on the local Ollama backend.

### SemanticRules: free local classification (no tokens)

`AiRules` costs tokens (or, with Ollama, CPU) on every file. **`SemanticRules`** is a free
stage that runs *before* `AiRules`: it embeds the document with a local ONNX model
(`fastembed`, no API key, offline after a one-time model download) and picks the category
whose example phrases are the closest match. Confident matches move for **$0**; only the
genuinely ambiguous files fall through to the paid LLM.

```yaml
SemanticRules:
  Model: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # multilingual, verified in fastembed 0.8.0
  SimilarityThreshold: 0.5           # below → fall through to AiRules
  EmbedSource: content               # content | filename | both
  Rules:
    - Title: "청구서"
      Utterances:                    # a few example phrases = the whole "training"
        - "세금계산서 공급가액 부가세 청구 금액"
        - "영수증 결제 내역 카드 승인"
      Actions:
        - MoveToFolder: ~/Documents/Invoices
    - Title: "설계문서"
      EmbedSource: filename          # per-rule override
      Utterances:
        - "설계문서 상세 설계 아키텍처"
      Actions:
        - MoveToFolder: ~/Documents/Design
```

Pipeline: `Rules` (filename, free) → `SemanticRules` (vector, free) → `AiRules` (LLM, paid fallback).

**`EmbedSource` — which text to embed** (measured on real docs):
- `content` (default) classifies by **topic** — great for topic-distinct categories
  (invoice vs contract vs resume).
- `filename` classifies by document **type/format**, which lives in the name
  ("주간업무보고", "설계문서"). Use it for same-topic/different-format categories that
  content embedding cannot separate.
- `both` concatenates filename + content (use sparingly — content can dilute the filename signal).

Notes:
- `Utterances` are short example phrases (3-6 per category), not the whole document. No
  labels, no training.
- If a filename always contains an exact keyword ("주간업무"), a plain `Rules`
  `FileNameContains` rule is simpler, exact, and free — reach for `SemanticRules` when the
  keyword varies (synonyms/typos).
- The first classified file downloads the embedding model (~240MB) to
  `~/.cache/folder-actions/fastembed`. All offline after that.
- See `examples/semantic.FolderActions.yaml` for a fuller sample.

**`FilenameStopwords` — strip noise before embedding** (filename source). Filenames often
carry organizational tokens on every file (team names, dept names, "final"/"draft"). Those
dominate the short filename embedding and drag classification the wrong way. List them under
`FilenameStopwords` and they are removed before embedding. Measured on real filenames:
without it 4/9, with the org-name list 8/9.

```yaml
SemanticRules:
  EmbedSource: filename
  FilenameStopwords:            # exact substrings removed from the filename before embedding
    - R&D Division
    - Team A
    - final
    - draft
  Rules: [...]
```

- List the **full org name**, not a bare shared word — a word that also appears in a real
  category would delete the category's signal.
- List overlapping stopwords **longest-first**, and write them **space-separated**
  (separators `_ - .` are normalized to spaces first).
- Numbers, dates, and week/period counters (`2026`, `0107`, `v2`, Korean `7월1주차`) are
  stripped **automatically** — no need to list them. (This means an alphanumeric code like
  `LM12` loses its digits; if a category depends on such a code, use an exact
  `FileNameContains` rule instead.)

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
