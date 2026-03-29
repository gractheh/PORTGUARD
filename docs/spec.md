# aicord — Software Engineering Specification

**Version:** 1.0
**Date:** 2026-03-29
**Status:** Current

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Component Specifications](#3-component-specifications)
4. [Data Model](#4-data-model)
5. [Configuration](#5-configuration)
6. [API Contracts](#6-api-contracts)
7. [Error Handling](#7-error-handling)
8. [Non-Functional Requirements](#8-non-functional-requirements)
9. [Extension Points](#9-extension-points)

---

## 1. System Overview

aicord is a single-user CLI application that provides an interactive, multi-turn conversation interface. The system prompt (defined in `docs/prompts.md`) configures the assistant as a dual-domain expert in software engineering and customs/trade compliance.

The application has two runtime components:

| Component | File | Responsibility |
|---|---|---|
| CLI Interface | `src/index.js` | Terminal I/O, user input handling, output streaming, REPL loop |
| AI Client | `src/claude.js` | Conversation history, prompt loading, response generation |

**Runtime environment:** Node.js ≥ 18.0.0 (ESM, `node --watch` for dev)
**No persistence:** All state is in-process; exits clean on termination.

---

## 2. Architecture

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│                        aicord                           │
│                                                         │
│  ┌──────────────────────┐    ┌───────────────────────┐  │
│  │     src/index.js     │    │    src/claude.js       │  │
│  │                      │    │                        │  │
│  │  readline REPL       │───▶│  chat(id, msg, opts)   │  │
│  │  ANSI formatting     │    │  reset(id)             │  │
│  │  command dispatch    │◀───│                        │  │
│  │  chunk streaming     │    │  ┌────────────────┐    │  │
│  └──────────────────────┘    │  │ histories: Map │    │  │
│                               │  └────────────────┘    │  │
│                               │  ┌────────────────┐    │  │
│                               │  │ SYSTEM_PROMPT  │    │  │
│                               │  │ (from .md file)│    │  │
│                               │  └────────────────┘    │  │
│                               └──────────┬────────────┘  │
└──────────────────────────────────────────┼───────────────┘
                                           │
                                           ▼
                              ┌────────────────────────┐
                              │   Local Response Engine │
                              │   (src/claude.js)       │
                              └────────────────────────┘
```

### 2.2 Data Flow — Single Turn

```
User types input
      │
      ▼
readline.question callback
      │
      ├─ command? (/reset, /help, /exit) → handle locally, re-prompt
      │
      └─ message → chat(SESSION, input, { onChunk })
                         │
                         ├─ append to history[]
                         │
                         ├─ client.messages.stream({
                         │     model, max_tokens, thinking,
                         │     system (cached), messages: history
                         │  })
                         │
                         ├─ stream.on('text', onChunk)
                         │       │
                         │       └─ process.stdout.write(chunk)  ← real-time
                         │
                         ├─ await stream.finalMessage()
                         │
                         ├─ extract text blocks from response.content
                         │
                         ├─ append assistant turn to history[]
                         │
                         └─ trim history if > MAX_HISTORY
```

### 2.3 Startup Sequence

```
node src/index.js
      │
      ├─ dotenv loads .env
      │
      ├─ claude.js module load:
      │     ├─ read docs/prompts.md synchronously
      │     └─ parse SYSTEM_PROMPT (split on '## System Prompt\n\n')
      │
      ├─ print banner
      │
      └─ prompt() — begin REPL
```

---

## 3. Component Specifications

### 3.1 `src/index.js` — CLI Interface

**Purpose:** Owns all terminal I/O. Has no knowledge of the response engine.

#### Exports
None. Entry point only.

#### Internal Functions

```
banner()
  Prints the application name, model info, and available commands.
  Called once at startup.

printHelp()
  Prints the /help text.
  Called when the user enters /help.

separator()
  Prints a horizontal rule between exchanges.
  Called after each assistant response.

main()  [async]
  Entry point. Validates env, prints banner, starts REPL.

prompt()
  Issues the readline question, receives user input, dispatches:
    - empty input   → re-prompt
    - /exit|/quit   → rl.close()
    - /reset        → reset(SESSION), re-prompt
    - /help         → printHelp(), re-prompt
    - anything else → chat(), stream output, re-prompt
```

#### Constants

| Name | Value | Purpose |
|---|---|---|
| `SESSION` | `'cli'` | Session key passed to `chat()`. Fixed for single-user CLI. |
| `c` | ANSI escape map | Terminal color codes. Keys: reset, bold, dim, cyan, green, yellow, gray, white. |

#### ANSI Color Usage

| Context | Color |
|---|---|
| User prompt label (`You:`) | cyan + bold |
| Assistant label (`Assistant:`) | bold |
| System messages (cleared, goodbye) | dim |
| Errors | yellow |
| Separator, model info | gray |

---

### 3.2 `src/claude.js` — AI Client

**Purpose:** Owns conversation state and response generation. Has no knowledge of the terminal.

#### Module-Level Initialization

Executed once at import time:

1. Reads `docs/prompts.md` synchronously via `readFileSync`.
2. Splits on `'## System Prompt\n\n'` and takes the second segment.
3. Falls back to `'You are a helpful assistant.'` if parsing fails.

**Failure mode:** If `docs/prompts.md` does not exist, `readFileSync` throws synchronously and the process exits before the REPL starts. This is intentional — the prompt is required.

#### Exports

```
reset(sessionId: string): void
  Deletes the history entry for sessionId.
  Safe to call when no history exists (no-op).

chat(
  sessionId: string,
  userMessage: string,
  opts?: { onChunk?: (text: string) => void }
): Promise<string>
  Appends userMessage to history, generates a response,
  fires onChunk for each text chunk, appends the assistant reply to history,
  trims history if over MAX_HISTORY, returns the full reply text.
```

#### Response Generation

Responses are generated locally. The `onChunk` callback fires synchronously with the complete reply text before `chat()` resolves.

---

## 4. Data Model

### 4.1 Conversation History

```
histories: Map<sessionId: string, messages: Message[]>

Message {
  role:    'user' | 'assistant'
  content: string   // plain text only; thinking blocks are not stored
}
```

**Invariants:**
- Messages alternate `user` / `assistant` (enforced by append order).
- The first message in any history is always `role: 'user'`.
- Content is always a plain string.
- History length is bounded at `MAX_HISTORY` (20 messages = 10 exchanges). When exceeded, the oldest two messages (one user + one assistant) are spliced off.

**Lifetime:** In-process only. Cleared on `/reset` or process exit.

### 4.2 System Prompt

Loaded once at module initialization from `docs/prompts.md`.

```
SYSTEM_PROMPT: string
  Extracted by splitting on '## System Prompt\n\n' and taking index [1].
  Sent on every API call as a cached system block.
  Never mutated at runtime.
```

### 4.3 Session Identity

The CLI uses a single fixed session key `'cli'`. This is the only session that exists in the single-user CLI context. The `sessionId` parameter in `src/claude.js` is designed to support multiple concurrent sessions if the interface layer is extended (see §9).

---

## 5. Configuration

### 5.1 Environment Variables

No required environment variables. `.env` is loaded via `dotenv` from the project root if present.

### 5.2 Hardcoded Constants

| Constant | Location | Value | Description |
|---|---|---|---|
| `MAX_HISTORY` | `src/claude.js` | `20` | Maximum messages retained per session |
| `SESSION` | `src/index.js` | `'cli'` | Session identifier for the CLI |

---

## 6. API Contracts

### 6.1 `chat()` — Full Specification

```
chat(sessionId, userMessage, opts?) → Promise<string>

Parameters:
  sessionId    string    Identifies the conversation. History is keyed on this.
  userMessage  string    The user's input. Must be non-empty (caller's responsibility).
  opts         object?   Optional.
    onChunk    function? Called with each text string chunk as it streams.
                         Invoked synchronously within the stream event loop.
                         Must not throw — errors propagate out of chat().

Returns:
  Promise<string>   The full assistant response text.
                    Returns '(No response.)' if no text is produced.

Throws:
  Does NOT throw on empty response — returns fallback string instead.

Side effects:
  - Mutates histories Map (appends user and assistant messages).
  - May mutate histories Map (splices oldest pair if over MAX_HISTORY).
  - Calls onChunk N times during streaming (if provided).
```

### 6.2 `reset()` — Full Specification

```
reset(sessionId) → void

Parameters:
  sessionId    string    The session to clear.

Side effects:
  - Deletes the entry from histories Map.
  - No-op if sessionId has no history.

Throws: never.
```

---

## 7. Error Handling

### 7.1 Startup Errors

| Condition | Behavior |
|---|---|
| `docs/prompts.md` not found | `readFileSync` throws synchronously, unhandled, process crashes with stack trace |
| `docs/prompts.md` missing `## System Prompt\n\n` | Fallback to `'You are a helpful assistant.'` |

### 7.2 Runtime Errors

All errors from `chat()` are caught in the `prompt()` callback in `src/index.js`:

```javascript
try {
  await chat(SESSION, input, { onChunk: (text) => out(text) });
} catch (err) {
  line();
  line(`  ${c.yellow}Error: ${err.message}${c.reset}`);
}
```

After displaying the error, the REPL calls `prompt()` and continues. The failed user message **remains in history** (appended before `chat()` was called). If this is undesirable for retry scenarios, the caller should pop the last history entry on error — this is a known gap (see §9).

### 7.3 Known Error Classes

| Condition | Common Cause | Behavior |
|---|---|---|
| Runtime error in `chat()` | Any unexpected failure | Displayed; session continues |

---

## 8. Non-Functional Requirements

### 8.1 Performance

| Metric | Target | Notes |
|---|---|---|
| Time to first token | < 100ms | Local generation |
| Input handling latency | < 1ms | readline + sync history append |
| Memory per session | < 50KB | 20 messages × ~1KB average |

### 8.2 Reliability

- **No local persistence** — a process crash loses conversation history only. No data corruption risk.
- **Stateless** — every call uses the full conversation history in-process.
- **Graceful degradation** — API errors display a message and continue the REPL; they do not crash the process.

### 8.3 Security

- `.env` is listed in `.gitignore`.
- No user input is executed as code, passed to shell commands, or written to disk.
- No network connections are made.

### 8.4 Compatibility

| Requirement | Value |
|---|---|
| Node.js minimum | 18.0.0 (ESM, `readline` async support) |
| Platform | macOS, Linux, Windows (ANSI codes render in Windows Terminal) |
| Shell | Any terminal emulator that supports ANSI escape codes |

---

## 9. Extension Points

The following are known natural extension points given the current architecture. None are implemented; all are scoped as future work.

### 9.1 Pop History on Error

**Gap:** When `chat()` throws, the user message has already been appended to history. A retry will produce a malformed `[user, user, ...]` sequence.

**Fix:** Expose a `popLast(sessionId)` function in `claude.js` and call it from the error handler in `index.js`.

### 9.2 Multiple Named Sessions

`chat()` and `reset()` already accept a `sessionId` parameter. A multi-session interface (e.g., named workspaces, per-project contexts) could be added to `index.js` without changing `claude.js`.

### 9.3 Configurable Response Parameters

Response behavior is currently fixed. These could be moved to environment variables or a `config.json` to support different usage profiles.

### 9.4 HTTP API Surface

`claude.js` has no I/O dependencies — it could be imported directly into an Express or Hono server to expose `chat()` and `reset()` as REST endpoints. The `sessionId` maps naturally to a bearer token or user ID.

### 9.5 Persistent History

History could be serialized to a JSON file on disk (keyed by session ID) to survive process restarts. The `Map<string, Message[]>` structure serializes trivially. Load on first `chat()` call for a given session, flush on `reset()` or process `SIGTERM`.

### 9.6 Prompt Hot-Reload

`SYSTEM_PROMPT` is read once at module load. For development, a `--watch` mode that re-reads `docs/prompts.md` on each turn (or on SIGHUP) would allow prompt iteration without restarting the process.
