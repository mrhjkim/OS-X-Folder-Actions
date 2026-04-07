# TODOs

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

**Depends on / blocked by:** The basic retroactive apply feature (this PR) must ship first.
