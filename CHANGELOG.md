# Changelog

All notable changes to this project will be documented in this file.

## [0.3.1.1] - 2026-07-22

### Fixed

- **`.xlsm` (macro-enabled Excel) content not extracted.** `ContentExtractor` dispatched
  only `.xlsx` to openpyxl, so `.xlsm`/`.xltx`/`.xltm` fell through to empty text and
  `AiRules` couldn't classify them. `.xlsm` is the same OOXML zip as `.xlsx` plus a macro
  part — openpyxl reads it directly, no new dependency. Real symptom:
  `20260721_SKT_지능망_Staging 기능 개발_개발3팀.xlsm` matched no Stage-1 rule, scored
  개발계획 0.514 in SemanticRules (below the 0.65 threshold, correct fallthrough), then
  reached `AiRules` with empty content and stayed unsorted. Now extracts
  "Staging 구성 기능 개발 …" so `AiRules` files it as 개발계획.

## [0.3.1.0] - 2026-07-20

### Added

- **`SimilarityMargin` — reject "no category fits" in SemanticRules.** The classifier is
  argmax over cosine similarity, so it always names the *nearest* rule even for a document
  that belongs to no configured category — and the tell is that every rule then scores about
  the same. `SimilarityThreshold` alone can't catch it: a cluster of rules all near 0.68
  squeaks past a 0.65 gate on a coin-flip winner. `SimilarityMargin` additionally requires
  the top rule to lead the runner-up by at least this much; a bunched result (small lead)
  falls through to `AiRules`. Real symptom: `26년 종합 이슈 내역_202607-개발1부.xlsx` scored
  주간보고 0.696 with all four rules inside 0.66–0.70 (lead 0.002), while every genuine match
  led by 0.3+. Default `0.0` keeps the old behavior; the gate is opt-in. 6 new tests.

## [0.3.0.2] - 2026-07-15

### Added

- **`.xls` content extraction (old binary Excel).** `ContentExtractor` handled only
  `.xlsx` (openpyxl); legacy `.xls` files fell through to empty text, so `AiRules` had
  nothing to classify and left them unsorted. Added an `xlrd`-based `.xls` reader
  (sheet 1, capped at 4096 chars, same shape as the `.xlsx` path). Real symptom:
  `SKT 지능망 PKG 개발 진행 상황.xls` now extracts "SKT 지능망 개발 계획 …" and `AiRules`
  files it as 개발계획 instead of leaving it in Downloads.
- `requirements.txt` gains `xlrd` (2.x reads only `.xls`; openpyxl keeps `.xlsx` — the
  two split the format cleanly). 4 new tests (mocked xlrd, no binary fixture).

## [0.3.0.1] - 2026-07-15

### Fixed

- **SemanticRules misclassified every Korean filename under the Folder Actions daemon.**
  macOS stores filenames decomposed (NFD) — `진행` arrives as separate jamo `ㅈㅣㄴㅎㅐㅇ`,
  identical on screen but different code points. The multilingual tokenizer split the
  decomposed form differently, so the daemon embedded a different vector than any shell
  test produced, and the NFC stopwords never matched the NFD filename. Real symptom:
  `SKT 지능망 PKG 개발 진행 상황.xls` embedded to 원인보고서 at 0.718 in the daemon while
  scoring 개발계획 0.546 in every CLI check. `_clean_filename` now folds the filename to
  NFC before stripping/embedding; utterances and extracted content are folded too, so
  both sides of the cosine are NFC. Only reproducible under the real GUI daemon, never in
  a shell — the daemon's stripped environment hands over the raw NFD path.

## [0.3.0.0] - 2026-07-15

### Added

- **SemanticRules — free local vector classification.** A new stage that runs before the
  paid `AiRules`: it embeds a file (its content and/or filename) with a local ONNX model
  (`fastembed`, no API key, offline after a one-time model download) and moves it to the
  category whose example phrases are the closest cosine match. Confident matches cost
  nothing; only ambiguous files fall through to the LLM. Pipeline is now
  `Rules → SemanticRules → AiRules → fallthrough`.
- Per-category `Utterances` (example phrases) defined right in `.FolderActions.yaml` — no
  labels, no training.
- **`EmbedSource`** field (`content` | `filename` | `both`), settable block-wide and
  overridable per rule. `content` classifies by topic (invoice vs contract); `filename`
  classifies by document type/format (weekly report vs design doc), which content
  embedding cannot separate. Measured on real documents before shipping.
- `SimilarityThreshold` gate; below it, the file falls through to `AiRules`.
- **`FilenameStopwords`** — substrings (org names, edit-state words) removed from the
  filename before embedding. Filenames carry organizational noise on nearly every file
  that dominates the short filename embedding; stripping it took real-file classification
  from 4/9 to 8/9. Numbers, dates, and week/period counters are stripped automatically.
- The embedding model cache is pinned to `~/.cache/folder-actions/fastembed` (fastembed's
  default is the system temp dir, which can be reaped mid-run).
- Dashboard reads, renders, and round-trips `SemanticRules`.
- `examples/semantic.FolderActions.yaml` sample config.
- 23 new tests (mocked embedder, no download in CI).

### Changed

- `requirements.txt` gains `fastembed`.
- `FolderActionsDashboard.parse_yaml_file` now returns a 3-tuple
  `(rules, ai_rules, semantic_rules)` and `rules_to_yaml` gains an optional
  `semantic_rules` argument.

## [0.2.0.0] - 2026-07-14

### Added

- **Gemini API backend for AiRules.** `AiRules` can now classify with Google's Gemini
  API instead of local Ollama. Set `Provider: gemini` and point `ApiKeyFile` at a key
  file outside the repo. Absent `Provider` keeps the existing Ollama behavior byte for
  byte — no config changes required.
- API key resolution: `GEMINI_API_KEY` env var first, then `ApiKeyFile`. The key file
  path is what makes classification work under the GUI Folder Actions daemon, which has
  no shell environment.
- Gemini responses are constrained with a `responseSchema` `enum` of the rule titles
  plus a `__NO_MATCH__` sentinel, so the model can never invent a rule that doesn't
  exist. `__NO_MATCH__` is a reserved rule title.
- `difflib` typo hints now extend to `AiRules` sub-keys (`ApiKeyfile` → `ApiKeyFile?`)
  and the `Provider` value (`gemni` → `gemini?`).
- One automatic retry on HTTP 429, honoring `Retry-After` (clamped to 0–30s).
- `examples/gemini.FolderActions.yaml` — a copy-paste sample config for the Gemini backend.
- 46 new tests. The Gemini eval (`scripts/eval_gemini_classifier.py`, real API key,
  excluded from the default `pytest` run) is described in the design doc and left for
  the maintainer to add before relying on the no-match path in production.

### Changed

- `AIProvider.query()` gained keyword-only `provider`, `api_key_file`, and a
  per-provider default `timeout` (60s for both backends). Existing callers are
  unaffected.
- `AIProvider` internals refactored into a backend registry. Ollama moved into
  `_backend_ollama` with its empty-response and parse-failure diagnostics preserved.
- `install.sh` now prints the Gemini key-file setup steps after installing.

### Fixed

- A hostile `Retry-After: -5` no longer reaches `time.sleep(-n)` (which raises and
  abandons the retry); the wait is clamped to 0.
- An `AiRules` rule with no `Title`, or one titled `__NO_MATCH__`, is now dropped at
  load time with a logged reason instead of only warned. The former crashed prompt
  building; the latter could never fire because it collided with the no-match sentinel.

### Security

- `Provider: gemini` uploads up to 4096 characters of each classified file's contents
  to Google. Documented in both READMEs; the local Ollama backend remains the default.
- The API key travels in the `x-goog-api-key` header, never a URL query parameter, so
  it cannot leak through a request exception that echoes the URL. Group/world-readable
  key files trigger a `chmod 600` warning.

## [0.1.0.0] - 2026-04-07

### Added

- **Retroactive Apply** — apply existing rules to files already sitting in watched folders.
  Previously, only new file additions triggered rule processing. Now you can preview which
  files haven't been processed yet and run the rule against them from the dashboard.
- `POST /api/retroactive` endpoint with `preview` and `run` actions.
- Per-rule card UI: "기존 파일 미리보기" (Preview) and "미처리 파일 실행" (Run) buttons.
- File status table showing per-file outcome: unprocessed / processed / skipped / run / error.
- Idempotency via audit log — files already logged as `success` or `intent` are skipped
  on re-run. AiAgent actions never fire twice on the same file.
- File count safety limits: preview ≤ 100 files, run ≤ 50 files (prevents blocking the
  single-threaded HTTP server during long AiAgent runs).
- Re-entry guard (`_retroRunning`) prevents double-click race condition on the Run button.
- 23 new tests covering all guards, preview/run logic, the CRITICAL `os.path.basename`
  regression (criteria must match filename-only, not full path), and all security fixes.
- `TODOS.md` — future enhancements tracking (SSE streaming for large folders).

### Fixed

- Audit log idempotency: `.FolderActions.py` writes the `"file"` key but
  `get_processed_files()` was reading `"item"` — every file appeared unprocessed,
  defeating the guard against re-running already-processed files. Now reads both keys.
- Source-index stability: retroactive POST now includes `folder_path`; server validates
  it matches the resolved source so concurrent log activity can't shift the index.
- CSS injection guard: `ruleId` validated as `/^r\d+$/` before DOM querySelector use.
- XSS: `modeLabel` and `critSummary()` output now HTML-escaped in `renderViewMode`.
- `bool` isinstance bypass: `True`/`False` no longer pass the `int` type check on
  `source_index` / `rule_index`.
- Module cache: `_load_folder_actions_module()` now caches after first load, preventing
  repeated `exec_module()` calls and file-handle leaks on high-frequency requests.
- Exception path in retroactive run no longer leaks file paths in error responses.
