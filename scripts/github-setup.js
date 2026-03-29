/**
 * github-setup.js
 *
 * Creates the aicord GitHub repo, labels, milestones, and all backlog issues
 * in one pass. Requires GITHUB_TOKEN and GITHUB_USERNAME in .env.
 *
 * Usage:
 *   node scripts/github-setup.js
 */

import 'dotenv/config';

const TOKEN    = process.env.GITHUB_TOKEN;
const USERNAME = process.env.GITHUB_USERNAME;
const REPO     = 'aicord';

if (!TOKEN || !USERNAME) {
  console.error('\nMissing credentials. Add to .env:\n  GITHUB_TOKEN=<personal access token>\n  GITHUB_USERNAME=<your github username>\n');
  process.exit(1);
}

const BASE    = 'https://api.github.com';
const HEADERS = {
  Authorization:        `Bearer ${TOKEN}`,
  Accept:               'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'Content-Type':       'application/json',
};

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: HEADERS,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${method} ${path} → HTTP ${res.status}: ${data.message ?? JSON.stringify(data)}`);
  return data;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function log(symbol, msg) { console.log(`  ${symbol}  ${msg}`); }

// ---------------------------------------------------------------------------
// Data — Labels (epics)
// ---------------------------------------------------------------------------

const LABELS = [
  { name: 'epic: core hardening',        color: 'b60205', description: 'E1 — CLI fixes, configurable params, prompt management' },
  { name: 'epic: conversation & memory', color: '0075ca', description: 'E2 — History persistence, named sessions, context management' },
  { name: 'epic: http api',              color: 'e4e669', description: 'E3 — REST interface and streaming endpoints' },
  { name: 'epic: web interface',         color: 'd876e3', description: 'E4 — Browser-based chat UI' },
  { name: 'epic: compliance tools',      color: '0e8a16', description: 'E5 — Structured trade data lookup tools' },
  { name: 'epic: developer experience',  color: '5319e7', description: 'E6 — Testing, CI, linting' },
];

// ---------------------------------------------------------------------------
// Data — Milestones (sprints)
// ---------------------------------------------------------------------------

const MILESTONES = [
  {
    title:       'Sprint 1 — Hardening & Foundation',
    description: 'Make the current CLI production-quality before extending it.',
  },
  {
    title:       'Sprint 2 — Persistence & Sessions',
    description: 'Conversations survive process restarts; multiple contexts supported.',
  },
  {
    title:       'Sprint 3 — HTTP API',
    description: 'Expose the assistant programmatically via REST and SSE.',
  },
  {
    title:       'Sprint 4 — Web Interface',
    description: 'Browser-accessible chat UI backed by the HTTP API.',
  },
  {
    title:       'Sprint 5 — Compliance Tools',
    description: 'Structured trade data tools that complement the AI responses.',
  },
];

// ---------------------------------------------------------------------------
// Data — Issues
// sprint: 1-indexed to match MILESTONES array position
// ---------------------------------------------------------------------------

const ISSUES = [
  // ── E1: Core Hardening ──────────────────────────────────────────────────
  {
    title:  'E1-1: Pop failed message from history on API error',
    labels: ['epic: core hardening'],
    sprint: 1,
    body: `## Context
When \`chat()\` throws an error, the user message has already been appended to history. A retry then produces a malformed \`[user, user, ...]\` sequence on the follow-up call.

## Changes required
- Add \`popLast(sessionId)\` export to \`src/claude.js\` that removes the last message from a session's history.
- Call \`popLast(SESSION)\` from the \`catch\` block in \`src/index.js\` before re-prompting.

## Acceptance criteria
- [ ] Triggering an API error (e.g. invalid key) and retrying the same input produces a valid response with no sequence error
- [ ] \`popLast\` is a no-op when history is empty
- [ ] Unit test covers the retry path`,
  },
  {
    title:  'E1-2: Make model parameters configurable via environment',
    labels: ['epic: core hardening'],
    sprint: 1,
    body: `## Context
\`model\`, \`max_tokens\`, and \`effort\` are hardcoded in \`src/claude.js\`. Users cannot tune these without modifying source.

## Changes required
Read the following env vars in \`src/claude.js\`, with documented defaults:

| Variable | Default | Valid values |
|---|---|---|
| \`AICORD_MODEL\` | — | Model identifier |
| \`AICORD_MAX_TOKENS\` | \`8192\` | Integer > 0 |
| \`AICORD_EFFORT\` | \`medium\` | \`low\`, \`medium\`, \`high\`, \`max\` |

Update \`.env.example\` with these variables (commented out).

## Acceptance criteria
- [ ] Setting any variable in \`.env\` changes the corresponding API call parameter
- [ ] Unset variables fall back to documented defaults
- [ ] Invalid \`AICORD_EFFORT\` value logs a warning and falls back to \`medium\``,
  },
  {
    title:  'E1-3: Prompt hot-reload in dev mode',
    labels: ['epic: core hardening'],
    sprint: 2,
    body: `## Context
\`SYSTEM_PROMPT\` is read once at module load. Iterating on the prompt requires a full process restart, which is slow during development.

## Changes required
When \`NODE_ENV=development\` (or a \`--watch-prompt\` flag is passed), re-read \`docs/prompts.md\` at the start of each \`chat()\` call instead of using the cached module-level constant.

## Acceptance criteria
- [ ] In dev mode, editing \`docs/prompts.md\` and sending a new message reflects the updated prompt without restarting
- [ ] In production mode (\`NODE_ENV\` unset or \`production\`), the prompt is still read once at startup
- [ ] Hot-reload path does not break prompt caching behaviour (cache_control still applied)`,
  },
  {
    title:  'E1-4: Validate prompts.md structure at startup',
    labels: ['epic: core hardening'],
    sprint: 1,
    body: `## Context
If \`docs/prompts.md\` is missing or does not contain \`## System Prompt\`, the process either crashes with an unhandled exception or silently falls back to a generic assistant prompt. Neither is acceptable.

## Changes required
Add an explicit startup check in \`src/claude.js\` (module initialization) that:
1. Verifies \`docs/prompts.md\` exists and is readable.
2. Verifies the parsed \`SYSTEM_PROMPT\` is non-empty and does not equal the fallback string.
3. Prints a clear, actionable error and exits with code 1 on failure.

## Acceptance criteria
- [ ] Missing file → descriptive error message referencing the expected path, exit code 1
- [ ] Missing \`## System Prompt\` section → descriptive error message, exit code 1
- [ ] Valid file → no output, startup proceeds normally`,
  },
  {
    title:  'E1-5: Add /history command to show current context size',
    labels: ['epic: core hardening'],
    sprint: 1,
    body: `## Context
Users have no visibility into how much conversation history is currently loaded. When troubleshooting unexpected responses, knowing the context window fill is useful.

## Changes required
- Add \`getHistory(sessionId)\` export to \`src/claude.js\` that returns \`{ exchanges: number, messages: number }\`.
- Add \`/history\` command to the REPL dispatch in \`src/index.js\`.
- Output: \`N exchanges in context (M messages)\`.
- Optionally append a rough token estimate (200 tokens/message average).

## Acceptance criteria
- [ ] \`/history\` prints the correct exchange count before and after adding messages
- [ ] \`/history\` after \`/reset\` shows 0 exchanges
- [ ] Command is listed in \`/help\` output`,
  },

  // ── E2: Conversation & Memory ────────────────────────────────────────────
  {
    title:  'E2-1: Persist conversation history to disk',
    labels: ['epic: conversation & memory'],
    sprint: 2,
    body: `## Context
All conversation history is lost when the process exits. Users must re-establish context on every session start.

## Changes required
- On each \`chat()\` call, serialize the session's history to \`.aicord/sessions.json\` (keyed by session ID) after appending the assistant reply.
- On module initialization, load existing sessions from this file if it exists.
- On \`reset(sessionId)\`, delete the session entry from the file.
- Create \`.aicord/\` directory if it does not exist.
- Add \`.aicord/\` to \`.gitignore\`.

## Acceptance criteria
- [ ] Closing and restarting the REPL resumes the previous conversation
- [ ] \`/reset\` clears both in-memory history and the persisted file entry
- [ ] Corrupted or missing \`.aicord/sessions.json\` logs a warning and starts fresh (does not crash)`,
  },
  {
    title:  'E2-2: Named sessions',
    labels: ['epic: conversation & memory'],
    sprint: 2,
    body: `## Context
The CLI uses a single fixed session key \`'cli'\`. Users who work on multiple projects or topics need independent conversation contexts.

## Changes required
Add session management commands to the REPL:
- \`/session <name>\` — switch to (or create) a named session
- \`/sessions\` — list all sessions with message counts
- \`/session new <name>\` — explicitly create a new session and switch to it

The active session name should appear in the prompt or banner.

## Acceptance criteria
- [ ] Switching sessions changes the active history; each session is independently persistent
- [ ] \`/sessions\` lists all session names with exchange counts
- [ ] Session names are alphanumeric + hyphens, max 64 chars; invalid names show an error
- [ ] Default session on fresh start is \`default\` (replaces hardcoded \`'cli'\`)`,
  },
  {
    title:  'E2-3: Session export to Markdown',
    labels: ['epic: conversation & memory'],
    sprint: 2,
    body: `## Context
Users want to save conversations as readable documents — for reference, sharing, or filing alongside a compliance decision.

## Changes required
Add \`/export\` command that writes the active session to \`.aicord/exports/<session>-<timestamp>.md\`.

Format:
\`\`\`markdown
# aicord session: <name>
Exported: <ISO timestamp>

---

**You:** <message>

**Assistant:** <reply>

---
\`\`\`

## Acceptance criteria
- [ ] \`/export\` creates a file in \`.aicord/exports/\` with correct formatting
- [ ] File opens correctly in a Markdown viewer
- [ ] Exporting an empty session produces a file with only the header
- [ ] File path is printed to the terminal after export`,
  },
  {
    title:  'E2-4: Auto-summarize old context when approaching history limit',
    labels: ['epic: conversation & memory'],
    sprint: 5,
    body: `## Context
The current history eviction strategy (splice oldest pair) loses information abruptly. Long working sessions lose context that may still be relevant.

## Changes required
When history length reaches \`MAX_HISTORY - 2\`, summarize the oldest 6 messages (3 exchanges) into a single compressed system-adjacent message, then replace those 6 messages with the summary.

Use a simple prompt: *"Summarize this conversation excerpt in 3–5 sentences, preserving key facts, decisions, and entities."*

## Acceptance criteria
- [ ] Conversations longer than 10 exchanges retain semantic continuity (test with a reference question about an early turn)
- [ ] Total history length stays bounded at \`MAX_HISTORY\`
- [ ] Summarization failure (API error) falls back to the existing splice behaviour and logs a warning`,
  },

  // ── E3: HTTP API ─────────────────────────────────────────────────────────
  {
    title:  'E3-1: Scaffold HTTP server with Hono',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
The assistant is currently CLI-only. An HTTP server enables programmatic access, browser clients, and integrations with external tools.

## Changes required
- Add \`hono\` as a dependency.
- Create \`src/server.js\` as an alternative entry point.
- Import \`chat\` and \`reset\` from \`src/claude.js\` — no duplication of AI logic.
- Add \`"server": "node src/server.js"\` to package.json scripts.
- Default port: 3000, configurable via \`PORT\` env var.

## Acceptance criteria
- [ ] \`npm run server\` starts an HTTP server on the configured port
- [ ] \`npm start\` still starts the CLI without modification
- [ ] \`GET /health\` returns \`{ status: "ok" }\` with HTTP 200`,
  },
  {
    title:  'E3-2: POST /chat endpoint (non-streaming)',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
Simple request/response interface for callers that do not need streaming.

## Specification
\`\`\`
POST /chat
Content-Type: application/json

{ "sessionId": "string", "message": "string" }

→ 200 OK
{ "reply": "string" }
\`\`\`

## Acceptance criteria
- [ ] Valid request returns the assistant's full reply as JSON
- [ ] Missing \`sessionId\` or \`message\` returns 400 with an error body
- [ ] Internal errors return 502 with an error body
- [ ] Empty \`message\` string returns 400`,
  },
  {
    title:  'E3-3: POST /chat/stream endpoint (Server-Sent Events)',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
Streaming allows browser clients and CLI consumers to display tokens as they arrive, matching the behaviour of the existing CLI.

## Specification
\`\`\`
POST /chat/stream
Content-Type: application/json

{ "sessionId": "string", "message": "string" }

→ 200 OK
Content-Type: text/event-stream

data: <chunk text>\\n\\n   (repeated per token)
data: [DONE]\\n\\n
\`\`\`

## Acceptance criteria
- [ ] EventSource or \`curl --no-buffer\` client receives tokens in real time
- [ ] Connection closes with \`[DONE]\` event after the response completes
- [ ] Same validation and error handling as \`POST /chat\`
- [ ] History is updated correctly (same as non-streaming path)`,
  },
  {
    title:  'E3-4: POST /reset endpoint',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
API clients need to be able to clear conversation history for a session.

## Specification
\`\`\`
POST /reset
Content-Type: application/json

{ "sessionId": "string" }

→ 200 OK
{ "ok": true }
\`\`\`

## Acceptance criteria
- [ ] After calling \`/reset\`, subsequent \`/chat\` calls start with empty history
- [ ] Missing \`sessionId\` returns 400
- [ ] Calling \`/reset\` on a session with no history is a no-op (returns 200)`,
  },
  {
    title:  'E3-5: Bearer token authentication middleware',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
The HTTP server must not be open to unauthenticated callers.

## Changes required
- Add bearer token authentication to all \`/chat\` and \`/reset\` routes.
- Token configured via \`AICORD_API_KEY\` environment variable.
- \`GET /health\` is exempt from authentication.

## Acceptance criteria
- [ ] Requests with \`Authorization: Bearer <correct token>\` succeed
- [ ] Requests with a wrong token return 401 \`{ "error": "Unauthorized" }\`
- [ ] Requests with no Authorization header return 401
- [ ] \`GET /health\` returns 200 without a token
- [ ] Server refuses to start if \`AICORD_API_KEY\` is not set`,
  },
  {
    title:  'E3-6: Structured JSON error responses',
    labels: ['epic: http api'],
    sprint: 3,
    body: `## Context
All API error paths must return consistent, machine-readable JSON.

## Error response shape
\`\`\`json
{ "error": "Human-readable message", "code": "ERROR_CODE" }
\`\`\`

## Error codes
| Scenario | HTTP | code |
|---|---|---|
| Missing required field | 400 | \`MISSING_FIELD\` |
| Empty message string | 400 | \`EMPTY_MESSAGE\` |
| Auth failure | 401 | \`UNAUTHORIZED\` |
| Internal error | 502 | \`UPSTREAM_ERROR\` |
| Unexpected server error | 500 | \`INTERNAL_ERROR\` |

## Acceptance criteria
- [ ] Every error path returns valid JSON with \`error\` and \`code\` fields
- [ ] HTTP status codes match the table above
- [ ] Internal error details are not leaked verbatim (log server-side, return a generic message to the client)`,
  },

  // ── E4: Web Interface ────────────────────────────────────────────────────
  {
    title:  'E4-1: Static HTML/CSS/JS chat UI',
    labels: ['epic: web interface'],
    sprint: 4,
    body: `## Context
A browser-based UI makes the assistant accessible without a terminal. No build step, no framework — vanilla HTML/CSS/JS consuming the SSE streaming endpoint.

## Requirements
- Message input (textarea, submits on Enter or button click)
- Send button
- Scrollable message history with user and assistant bubbles styled distinctly
- Streaming text renders token-by-token using the \`/chat/stream\` SSE endpoint
- Loading indicator while waiting for first token

## Acceptance criteria
- [ ] Opening \`http://localhost:3000\` shows a functional chat interface
- [ ] Messages stream in real time (tokens visible as they arrive)
- [ ] Sending a message while a response is streaming is disabled (debounced)
- [ ] UI is usable on a 1280px viewport without horizontal scroll`,
  },
  {
    title:  'E4-2: Serve static files from HTTP server',
    labels: ['epic: web interface'],
    sprint: 4,
    body: `## Context
The Hono server needs to serve the static UI files.

## Changes required
- Mount \`src/public/\` as static assets on the Hono server.
- \`GET /\` serves \`src/public/index.html\`.

## Acceptance criteria
- [ ] \`GET /\` returns the HTML file with correct \`Content-Type: text/html\`
- [ ] CSS and JS assets load correctly (no 404s in browser console)
- [ ] Static file serving does not interfere with API routes`,
  },
  {
    title:  'E4-3: Persist session ID in browser localStorage',
    labels: ['epic: web interface'],
    sprint: 4,
    body: `## Context
Without persistence, each page reload starts a new session and loses conversation context.

## Changes required
- On first load, generate a UUID session ID and store it in \`localStorage\` under \`aicord_session_id\`.
- On subsequent loads, read the stored ID and use it for all API calls.
- Add a "New conversation" button that generates a new UUID and clears the visible message history.

## Acceptance criteria
- [ ] Reloading the page reconnects to the existing server-side session
- [ ] "New conversation" generates a new session ID, calls \`POST /reset\` on the old one, and clears the UI
- [ ] Opening a second browser tab creates a separate session`,
  },
  {
    title:  'E4-4: Render Markdown in assistant message bubbles',
    labels: ['epic: web interface'],
    sprint: 4,
    body: `## Context
Assistant responses contain Markdown formatting (bold, code blocks, lists, tables). Displaying raw Markdown is unreadable.

## Changes required
- Include \`marked.js\` via CDN (no build step).
- Parse and render assistant message content as HTML before inserting into the DOM.
- Sanitize rendered HTML to prevent XSS (use \`DOMPurify\` via CDN).

## Acceptance criteria
- [ ] Fenced code blocks render with monospace font and background shading
- [ ] Bold, italic, and inline code render correctly
- [ ] Unordered and ordered lists render correctly
- [ ] Raw \`**\` or \`\`\` characters are not visible in rendered output
- [ ] XSS payload in assistant response is sanitized (does not execute)`,
  },
  {
    title:  'E4-5: Copy-to-clipboard button on assistant messages',
    labels: ['epic: web interface'],
    sprint: 4,
    body: `## Context
Users frequently want to copy assistant responses to paste into documents, emails, or code editors.

## Changes required
- Add a copy icon button to each assistant message bubble (appears on hover).
- Clicking copies the raw Markdown text (not the rendered HTML) to the clipboard.
- Button shows a brief checkmark/confirmation after successful copy.

## Acceptance criteria
- [ ] Clicking copy places the raw Markdown text on the clipboard
- [ ] Confirmation feedback is visible for ≥ 1 second after copy
- [ ] Button is keyboard-accessible (focusable, activates on Enter/Space)`,
  },

  // ── E5: Compliance Tools ─────────────────────────────────────────────────
  {
    title:  'E5-1: HS/HTSUS chapter reference tool',
    labels: ['epic: compliance tools'],
    sprint: 5,
    body: `## Context
Users frequently need to look up HS chapter titles and scope when classifying goods. Having this available as a command reduces context-switching to external references.

## Changes required
- Bundle a static JSON file mapping HS chapter numbers (01–99) to their titles, section assignments, and representative goods descriptions.
- Add \`/hs <chapter>\` command to the CLI REPL.
- The AI may also reference this data when answering classification questions.

## Acceptance criteria
- [ ] \`/hs 84\` returns the chapter title, section number, and 3–5 representative goods examples
- [ ] Invalid chapter numbers show a clear error
- [ ] Data covers all 99 HS chapters`,
  },
  {
    title:  'E5-2: Duty rate quick-reference by HTSUS subheading',
    labels: ['epic: compliance tools'],
    sprint: 5,
    body: `## Context
After classifying a product, users need the applicable duty rate. The current assistant can approximate this from training data but a structured reference prevents errors.

## Changes required
- Bundle a static dataset of HTSUS subheadings with NTR (MFN) general rates, special program rates (GSP, USMCA, etc.), and column 2 rates.
- Source: public CBP/USITC data.
- Add \`/duty <subheading>\` command (accepts both \`8471.30\` and \`8471.30.0100\` formats).

## Acceptance criteria
- [ ] \`/duty 8471.30.0100\` returns the NTR rate, applicable special program codes, and column 2 rate
- [ ] Partial subheading lookup (e.g. \`8471.30\`) returns all matching 10-digit subheadings
- [ ] Data freshness date is displayed with results`,
  },
  {
    title:  'E5-3: Section 301 tariff status by HTSUS subheading',
    labels: ['epic: compliance tools'],
    sprint: 5,
    body: `## Context
Section 301 China tariffs are the most significant additional duty layer for most importers and change frequently. Surfacing this automatically reduces errors.

## Changes required
- Bundle a dataset of HTSUS subheadings subject to Section 301 Lists 1, 2, 3, and 4A, with current rates and any active exclusion status.
- When the AI answers a classification question, automatically check whether the identified subheading appears on a 301 list and include this in the response.
- Add \`/301 <subheading>\` command for direct lookup.

## Acceptance criteria
- [ ] \`/301 8471.30.0100\` returns list membership, current rate, and exclusion status
- [ ] Subheadings not on any list return a clear negative result
- [ ] Dataset includes a last-updated date; stale data (> 90 days) triggers a warning`,
  },
  {
    title:  'E5-4: Denied-party name screening (local, public lists)',
    labels: ['epic: compliance tools'],
    sprint: 5,
    body: `## Context
Basic name screening against public denied-party lists is a fundamental export compliance step. Having it in the CLI reduces friction for quick pre-transaction checks.

## Changes required
- Bundle a local snapshot of:
  - OFAC SDN list (name + aliases)
  - BIS Entity List (name + country)
- Add \`/screen <name>\` command that performs fuzzy name matching against both lists.
- Display match results with list source, country, and the reason for listing.
- Note clearly that this is not a substitute for a full screening program.

## Acceptance criteria
- [ ] Screening a known SDN name returns a match with list, program, and entry details
- [ ] Screening a clearly clean name returns no results
- [ ] Partial name matches are flagged as potential matches (not definitive hits)
- [ ] Dataset includes a last-updated timestamp`,
  },
  {
    title:  'E5-5: INCOTERMS quick reference',
    labels: ['epic: compliance tools'],
    sprint: 5,
    body: `## Context
INCOTERMS are referenced constantly in trade discussions. A structured reference prevents common misinterpretations (particularly around risk transfer and insurance obligations).

## Changes required
- Bundle INCOTERMS 2020 data: all 11 terms with seller/buyer obligation matrix, risk transfer point, cost allocation, and typical use cases.
- Add \`/incoterms <term>\` command (e.g. \`/incoterms DDP\`, \`/incoterms CIF\`).
- Add \`/incoterms\` with no argument to list all terms with one-line descriptions.

## Acceptance criteria
- [ ] \`/incoterms DDP\` returns seller obligations, buyer obligations, risk transfer point, and cost split
- [ ] \`/incoterms\` lists all 11 terms
- [ ] Invalid term shows an error with the list of valid terms
- [ ] Output notes the INCOTERMS 2020 version`,
  },

  // ── E6: Developer Experience ─────────────────────────────────────────────
  {
    title:  'E6-1: Add ESLint with flat config',
    labels: ['epic: developer experience'],
    sprint: 1,
    body: `## Context
No linting is configured. Inconsistent style and preventable errors (unused vars, undeclared variables) accumulate without it.

## Changes required
- Add \`eslint\` as a dev dependency.
- Create \`eslint.config.js\` (flat config) for ESM.
- Rules: \`no-unused-vars\`, \`no-undef\`, \`prefer-const\`, \`eqeqeq\`.
- Add \`"lint": "eslint src/"\` to package.json scripts.

## Acceptance criteria
- [ ] \`npm run lint\` runs cleanly on the current codebase with no errors
- [ ] Introducing an unused variable causes \`npm run lint\` to fail`,
  },
  {
    title:  'E6-2: Add unit test suite with Node test runner',
    labels: ['epic: developer experience'],
    sprint: 1,
    body: `## Context
No tests exist. The core logic in \`src/claude.js\` and the CLI utilities in \`src/index.js\` should be covered before extending the system.

## Changes required
Use Node's built-in \`node:test\` runner. Create \`test/\` directory. Write tests for:

- \`reset()\` — clears history, no-op on unknown session
- \`chat()\` — appends correct message roles, trims history at \`MAX_HISTORY\`, returns text
- \`splitMessage()\` — splits at newlines, exact-length boundary, no trailing whitespace
- \`popLast()\` (after E1-1) — removes last message, no-op on empty history

Mock the chat module where needed (replace with a stub).

- Add \`"test": "node --test test/"\` to package.json scripts.

## Acceptance criteria
- [ ] \`npm test\` runs and all tests pass
- [ ] A broken \`reset()\` implementation causes at least one test to fail`,
  },
  {
    title:  'E6-3: GitHub Actions CI workflow',
    labels: ['epic: developer experience'],
    sprint: 3,
    body: `## Context
No automated checks run on push or pull request. Regressions can be merged without detection.

## Changes required
Create \`.github/workflows/ci.yml\` that runs on push and pull request to \`main\`:

\`\`\`yaml
- Node.js 22.x
- npm ci
- npm run lint
- npm test
\`\`\`

## Acceptance criteria
- [ ] Workflow passes on the current codebase
- [ ] A failing test causes the workflow to fail and blocks merge (branch protection)
- [ ] A lint error causes the workflow to fail`,
  },
  {
    title:  'E6-4: Consolidate npm scripts',
    labels: ['epic: developer experience'],
    sprint: 5,
    body: `## Context
Scripts accumulate across sprints. They should be consolidated into a consistent set before the project grows further.

## Target scripts
\`\`\`json
{
  "start":   "node src/index.js",
  "server":  "node src/server.js",
  "dev":     "node --watch src/index.js",
  "lint":    "eslint src/",
  "test":    "node --test test/",
  "check":   "npm run lint && npm run test"
}
\`\`\`

## Acceptance criteria
- [ ] All six scripts are present and functional
- [ ] \`npm run check\` passes on a clean codebase
- [ ] \`npm run check\` fails if lint or tests fail`,
  },
];

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

async function createRepo() {
  console.log('\n  Creating repository...');
  const repo = await api('POST', '/user/repos', {
    name:        REPO,
    description: 'Elite AI assistant for software engineering and customs/trade compliance',
    private:     false,
    auto_init:   false,
  });
  log('✓', `Created ${repo.full_name}`);
  return repo;
}

async function createLabels() {
  console.log('\n  Creating labels...');
  for (const label of LABELS) {
    await api('POST', `/repos/${USERNAME}/${REPO}/labels`, label);
    log('✓', `Label: ${label.name}`);
    await sleep(150);
  }
}

async function createMilestones() {
  console.log('\n  Creating milestones...');
  const numbers = [];
  for (const ms of MILESTONES) {
    const created = await api('POST', `/repos/${USERNAME}/${REPO}/milestones`, ms);
    numbers.push(created.number);
    log('✓', `Milestone ${created.number}: ${ms.title}`);
    await sleep(150);
  }
  return numbers; // [sprint1Number, sprint2Number, ...]
}

async function createIssues(milestoneNumbers) {
  console.log('\n  Creating issues...');
  for (const issue of ISSUES) {
    const payload = {
      title:     issue.title,
      body:      issue.body,
      labels:    issue.labels,
      milestone: milestoneNumbers[issue.sprint - 1],
    };
    const created = await api('POST', `/repos/${USERNAME}/${REPO}/issues`, payload);
    log('✓', `#${created.number} ${issue.title}`);
    await sleep(250); // GitHub secondary rate limit: be gentle
  }
}

async function main() {
  console.log(`\n  aicord GitHub setup`);
  console.log(`  Repo: ${USERNAME}/${REPO}`);

  try {
    const repo             = await createRepo();
    await createLabels();
    const milestoneNumbers = await createMilestones();
    await createIssues(milestoneNumbers);

    console.log(`\n  ✓ Done.\n`);
    console.log(`  Repository: ${repo.html_url}`);
    console.log(`  Issues:     ${repo.html_url}/issues`);
    console.log(`  Milestones: ${repo.html_url}/milestones\n`);
    console.log(`  Next steps:`);
    console.log(`    git remote add origin ${repo.clone_url}`);
    console.log(`    git push -u origin master\n`);

  } catch (err) {
    console.error(`\n  ✗ ${err.message}\n`);
    process.exit(1);
  }
}

main();
