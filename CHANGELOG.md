# Changelog

All notable changes to this project will be documented in this file.

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
