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
- 15 new tests covering all guards, preview/run logic, and the CRITICAL `os.path.basename`
  regression (criteria must match filename-only, not full path).
- `TODOS.md` — future enhancements tracking (SSE streaming for large folders).
