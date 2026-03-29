# aicord — Product Backlog

## Epics

| ID | Epic | Description |
|----|------|-------------|
| E1 | Core Hardening | Fix known gaps in the current CLI, make parameters configurable, improve DX |
| E2 | Conversation & Memory | Persistent history, named sessions, session management commands |
| E3 | HTTP API | REST interface so the assistant can be called programmatically or by other tools |
| E4 | Web Interface | Browser-based chat UI backed by the HTTP API |
| E5 | Compliance Tools | Structured lookup tools that augment the AI (HS search, duty calculator, entity screening) |
| E6 | Developer Experience | Testing, CI, linting, contribution docs |

---

## Issues

### E1 — Core Hardening

**E1-1: Pop failed message from history on API error**
When `chat()` throws, the user message has already been appended to history, causing a malformed `[user, user, ...]` sequence on retry. Expose `popLast(sessionId)` in `claude.js` and call it from the error handler in `index.js`.
- Acceptance: retrying the same input after an API error produces a valid response with no sequence error

**E1-2: Make model parameters configurable via environment**
`model`, `max_tokens`, and `effort` are hardcoded in `src/claude.js`. Move them to environment variables with documented defaults so users can tune without touching source.
- `AICORD_MODEL` (default: `claude-opus-4-6`)
- `AICORD_MAX_TOKENS` (default: `8192`)
- `AICORD_EFFORT` (default: `medium`, accepts `low | medium | high | max`)
- Acceptance: setting any variable in `.env` changes the corresponding API call parameter; unset falls back to default

**E1-3: Prompt hot-reload in dev mode**
`SYSTEM_PROMPT` is read once at module load. Add a `--watch-prompt` flag (or honor `NODE_ENV=development`) that re-reads `docs/prompts.md` on each turn, so prompt changes take effect without restarting.
- Acceptance: editing `docs/prompts.md` while the REPL is running and sending a message reflects the new prompt

**E1-4: Validate prompts.md structure at startup**
If `docs/prompts.md` is missing or does not contain the `## System Prompt` section, the process currently crashes with an unhandled error or silently falls back. Add an explicit startup check with a clear, actionable error message.
- Acceptance: missing file or missing section prints a descriptive error and exits with code 1

**E1-5: Add /history command to show current context size**
Users have no visibility into how much conversation history is loaded. Add a `/history` command that prints the current number of exchanges and approximate token estimate.
- Acceptance: `/history` prints `N exchanges in context (approx. X tokens)`; works after /reset

---

### E2 — Conversation & Memory

**E2-1: Persist conversation history to disk**
History is currently lost on process exit. Serialize `histories` Map to a JSON file (e.g., `.aicord/sessions.json`) on each turn and load it at startup. Use the session ID as the key.
- Acceptance: closing and restarting the REPL resumes the previous conversation; `/reset` clears both memory and the file entry

**E2-2: Named sessions**
Allow users to maintain multiple named conversation contexts (e.g., one for a specific project, one for general questions). Add `/session <name>` to switch, `/sessions` to list, `/session new <name>` to create.
- Acceptance: switching sessions changes the active history; each session is independently persistent

**E2-3: Session export**
Add `/export` command that writes the current session's conversation to a Markdown file (`.aicord/exports/<session>-<timestamp>.md`) with user/assistant turns formatted as readable dialogue.
- Acceptance: exported file contains full conversation with timestamps; can be opened in any Markdown viewer

**E2-4: Auto-summarize old context**
When history approaches `MAX_HISTORY`, instead of simply evicting the oldest exchange, summarize the oldest N exchanges into a single compressed context block and prepend it to the history. This preserves long-term context at lower token cost.
- Acceptance: conversations longer than 10 exchanges retain semantic continuity; token count does not grow unboundedly

---

### E3 — HTTP API

**E3-1: Scaffold HTTP server (Hono)**
Add Hono as a dependency and create `src/server.js` as an alternative entry point to `src/index.js`. The server should import `chat` and `reset` from `src/claude.js` — no duplication of AI logic.
- Acceptance: `node src/server.js` starts an HTTP server; `node src/index.js` still starts the CLI

**E3-2: POST /chat endpoint**
```
POST /chat
Body: { sessionId: string, message: string }
Response: { reply: string }
```
Non-streaming. Returns the full reply after the model finishes.
- Acceptance: curl with a valid body returns a JSON response with the assistant's reply

**E3-3: POST /chat/stream endpoint (SSE)**
```
POST /chat/stream
Body: { sessionId: string, message: string }
Response: text/event-stream
  data: <chunk>\n\n  (repeated)
  data: [DONE]\n\n
```
Server-sent events for streaming responses.
- Acceptance: EventSource client receives tokens in real time; connection closes after `[DONE]`

**E3-4: POST /reset endpoint**
```
POST /reset
Body: { sessionId: string }
Response: { ok: true }
```
- Acceptance: subsequent chat calls start with empty history

**E3-5: API key authentication middleware**
Add bearer token authentication to all API routes. Token configured via `AICORD_API_KEY` environment variable. Requests without a valid token receive 401.
- Acceptance: requests with correct `Authorization: Bearer <token>` succeed; requests without it return 401

**E3-6: Request validation and error responses**
All endpoints return structured JSON errors:
```json
{ "error": "message string", "code": "ERROR_CODE" }
```
- Missing fields → 400
- Auth failure → 401
- Anthropic API error → 502
- Acceptance: every error path returns valid JSON with an appropriate HTTP status code

---

### E4 — Web Interface

**E4-1: Static HTML/CSS/JS chat UI**
Create `src/public/index.html` — a single-page chat interface served by the HTTP server. No build step, no framework. Vanilla JS using the SSE streaming endpoint.
- Requirements: message input, send button, scrollable message history, streaming text rendering
- Acceptance: opening `http://localhost:3000` in a browser produces a functional chat interface

**E4-2: Serve static files from HTTP server**
Mount `src/public/` as static files on the Hono server.
- Acceptance: `GET /` serves `index.html`; assets load correctly

**E4-3: Session persistence in the UI**
Store the `sessionId` in `localStorage` so the browser reconnects to the same server-side session across page reloads.
- Acceptance: reloading the page continues the existing conversation

**E4-4: Markdown rendering in chat bubbles**
Assistant responses contain Markdown formatting (bold, code blocks, lists). Render them as HTML in the UI using a lightweight client-side Markdown parser (e.g., marked.js via CDN).
- Acceptance: fenced code blocks render with monospace font; bold/italic renders correctly; raw `**` characters are not visible

**E4-5: Copy-to-clipboard on assistant messages**
Add a copy button on each assistant message bubble.
- Acceptance: clicking copy places the raw Markdown text (not HTML) on the clipboard

---

### E5 — Compliance Tools

**E5-1: HS/HTSUS chapter reference tool**
Add a built-in lookup that maps HS chapter numbers to their titles and common goods. Accessible via `/hs <chapter>` in the CLI or as context injected into relevant queries.
- Acceptance: `/hs 84` returns chapter title, section, and representative goods; the AI can reference this in classification questions

**E5-2: Duty rate quick-reference**
Integrate a static duty rate reference (NTR/MFN rates from the HTSUS) that can be queried by subheading. Updated periodically from public CBP data.
- Acceptance: `/duty 8471.30.0100` returns the NTR rate, applicable special programs, and any active Section 301/232 adders

**E5-3: Section 301 tariff tracker**
Maintain a local dataset of active Section 301 List 1–4A subheadings and their rates. Surface this automatically when a classification question involves China-origin goods.
- Acceptance: queries about goods classified under active 301 subheadings include the applicable additional duty in the response

**E5-4: Denied-party name screening (local)**
Add a `/screen <entity name>` command that checks a name against a bundled subset of public screening lists (OFAC SDN, BIS Entity List). Returns matches with list source and details.
- Acceptance: screening a known SDN name returns a match with the list entry; screening a clearly clean name returns no results; false-positive handling is noted

**E5-5: INCOTERMS quick reference**
Add `/incoterms <term>` command that returns a structured summary of the term's obligations, risk transfer point, and typical use cases.
- Acceptance: `/incoterms DDP` returns seller/buyer obligations, risk transfer, and cost allocation summary

---

### E6 — Developer Experience

**E6-1: Add ESLint**
Configure ESLint with a flat config (`eslint.config.js`) for ESM. Rules: no unused vars, no undef, consistent spacing, prefer `const`.
- Acceptance: `npm run lint` runs cleanly on the existing codebase

**E6-2: Add test suite (Node test runner)**
Use Node's built-in `node:test` runner (no Jest/Vitest). Write unit tests for:
- `reset()` — clears history
- `chat()` — appends correct message structure, trims history at limit
- `splitMessage()` — splits at newlines, handles edge cases
- Acceptance: `npm test` runs and all tests pass; tests mock the Anthropic client

**E6-3: GitHub Actions CI**
Add `.github/workflows/ci.yml` that runs lint and tests on push and pull request to `main`.
- Acceptance: workflow passes on the current codebase; a failing test causes the workflow to fail

**E6-4: Add npm scripts for common tasks**
```json
"lint":    "eslint src/",
"test":    "node --test test/",
"check":   "npm run lint && npm run test"
```
- Acceptance: all three scripts work from the project root

---

## Sprints

### Sprint 1 — Hardening & Foundation
*Goal: make the current CLI production-quality before extending it*

| Issue | Title |
|-------|-------|
| E1-1 | Pop failed message from history on API error |
| E1-2 | Make model parameters configurable via environment |
| E1-4 | Validate prompts.md structure at startup |
| E1-5 | Add /history command |
| E6-1 | Add ESLint |
| E6-2 | Add test suite |

### Sprint 2 — Persistence & Sessions
*Goal: conversations survive process restarts; multiple contexts supported*

| Issue | Title |
|-------|-------|
| E2-1 | Persist conversation history to disk |
| E2-2 | Named sessions |
| E2-3 | Session export |
| E1-3 | Prompt hot-reload in dev mode |

### Sprint 3 — HTTP API
*Goal: expose the assistant programmatically*

| Issue | Title |
|-------|-------|
| E3-1 | Scaffold HTTP server (Hono) |
| E3-2 | POST /chat endpoint |
| E3-3 | POST /chat/stream endpoint (SSE) |
| E3-4 | POST /reset endpoint |
| E3-5 | API key authentication middleware |
| E3-6 | Request validation and error responses |
| E6-3 | GitHub Actions CI |

### Sprint 4 — Web Interface
*Goal: browser-accessible chat UI*

| Issue | Title |
|-------|-------|
| E4-1 | Static HTML/CSS/JS chat UI |
| E4-2 | Serve static files from HTTP server |
| E4-3 | Session persistence in the UI |
| E4-4 | Markdown rendering in chat bubbles |
| E4-5 | Copy-to-clipboard on assistant messages |

### Sprint 5 — Compliance Tools
*Goal: structured trade data tools that complement the AI*

| Issue | Title |
|-------|-------|
| E5-1 | HS/HTSUS chapter reference tool |
| E5-2 | Duty rate quick-reference |
| E5-3 | Section 301 tariff tracker |
| E5-4 | Denied-party name screening (local) |
| E5-5 | INCOTERMS quick reference |
| E2-4 | Auto-summarize old context |
| E6-4 | Add npm scripts for common tasks |
