import Anthropic from '@anthropic-ai/sdk';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));

// Load the system prompt from docs/prompts.md at startup.
// Everything after "## System Prompt\n\n" is the prompt body.
const promptFile = readFileSync(join(__dir, '../docs/prompts.md'), 'utf-8');
const SYSTEM_PROMPT = promptFile.split('## System Prompt\n\n')[1]?.trim()
  ?? 'You are a helpful assistant.';

const client = new Anthropic();

// Conversation history per session key.
// Map<sessionId, Array<{role: 'user'|'assistant', content: string}>>
const histories = new Map();
const MAX_HISTORY = 20; // 10 exchanges

/** Clear conversation history for a session. */
export function reset(sessionId) {
  histories.delete(sessionId);
}

/**
 * Send a message and stream the response.
 *
 * @param {string}   sessionId    Unique key for this conversation (e.g. 'cli')
 * @param {string}   userMessage  The user's input
 * @param {object}   [opts]
 * @param {function} [opts.onChunk]  Called with each text chunk as it streams
 * @returns {Promise<string>}  The full response text
 */
export async function chat(sessionId, userMessage, { onChunk } = {}) {
  if (!histories.has(sessionId)) histories.set(sessionId, []);
  const history = histories.get(sessionId);

  history.push({ role: 'user', content: userMessage });

  const stream = client.messages.stream({
    model: 'claude-opus-4-6',
    max_tokens: 8192,
    thinking: { type: 'adaptive' },
    output_config: { effort: 'medium' },
    system: [
      {
        type: 'text',
        text: SYSTEM_PROMPT,
        // Cache the system prompt — it's large and identical across every turn.
        cache_control: { type: 'ephemeral' },
      },
    ],
    messages: history,
  });

  // Stream text chunks in real time; thinking blocks are silently skipped.
  if (onChunk) {
    stream.on('text', onChunk);
  }

  const response = await stream.finalMessage();

  const replyText = response.content
    .filter(b => b.type === 'text')
    .map(b => b.text)
    .join('');

  history.push({ role: 'assistant', content: replyText });

  // Evict the oldest exchange once the cap is reached.
  while (history.length > MAX_HISTORY) {
    history.splice(0, 2);
  }

  return replyText || '(No response.)';
}
