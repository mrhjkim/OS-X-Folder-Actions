# TODOs

## Security / Correctness

### Server-side Run Lock (multi-tab concurrency)

**What:** Add a `threading.Lock()` and a `_running_rule_keys` set in the server to prevent
two concurrent retroactive run requests from firing `apply_rule` on the same files simultaneously.

**Why:** The client-side `_retroRunning` guard only protects within a single browser tab.
Two tabs or a page reload mid-run can produce concurrent requests. The single-threaded
`BaseHTTPRequestHandler` serializes connections, but keep-alive behavior may allow overlap.

**Priority:** P3 — low probability in a local single-user dashboard.

### RunShellScript warning on retroactive run

**What:** When a rule's actions include `RunShellScript` or `AiAgent`, show a confirmation
modal before the retroactive run fires (these execute arbitrary commands, not just file moves).

**Why:** The "Run" button label gives no indication shell scripts will execute. A user
who clicks it expecting a simple file move will be surprised.

**Priority:** P2 — UX correctness, especially relevant for rules with `AiAgent` actions.

---

## Future Enhancements

### Streaming/SSE for Retroactive Apply (large folders)

**What:** Replace the synchronous `/api/retroactive?action=run` response with Server-Sent
Events (SSE), streaming per-file progress as each file is processed.

**Why:** The current implementation is limited to 50 files per run (enforced with a 400
error) because `BaseHTTPRequestHandler` is single-threaded — a 50-file AiAgent run could
block the server for 6000 seconds. SSE would let the server push progress updates while
the run executes, eliminating the need for the file count cap.

**Pros:**
- Removes the 50-file limit for retroactive runs
- User sees real-time progress instead of waiting for a single response
- Dashboard stays responsive during long runs

**Cons:**
- Requires refactoring `_handle_retroactive()` to stream via chunked HTTP
- JS side needs EventSource instead of fetch
- More complex to test

**Context:**
- Current workaround: `MAX_RETROACTIVE_RUN_FILES = 50` enforced in `_handle_retroactive()`
- Design doc: `~/.gstack/projects/mrhjkim-OS-X-Folder-Actions/mrhjkim-master-design-20260407-171712.md`
- Only matters when a watched folder has >50 files matching a rule


---

### Gemini rate-limit backoff (multi-file bursts)

**What:** When a burst of files is dropped at once, the Gemini backend can hit the
free-tier per-minute request cap. Current behavior: one retry honoring `Retry-After`
(capped 30s), then `_error` and the file is left unclassified.

**Why:** A scanner dumping 20+ documents into a watched folder will exhaust the
per-minute quota partway through, so the later files silently fall through to Stage 3.

**Possible approaches:**
- Exponential backoff with more than one retry (bounded, so the daemon doesn't hang)
- A small client-side token-bucket throttle across consecutive drops
- Batch classification (one call for N files) once the API supports it well

**Priority:** P3 — only matters on large bursts; single drops are unaffected.

**Context:**
- Retry logic: `_backend_gemini()` in `AIProvider.py` (429 → `Retry-After`, one retry)
- Design doc: `~/.gstack/projects/mrhjkim-OS-X-Folder-Actions/mrhjkim-master-design-20260710-153648.md`
