/**
 * Local chat module — no external API calls.
 * Provides the same chat/reset interface as the original module.
 */

// Conversation history per session key.
const histories = new Map();
const MAX_HISTORY = 20;

/** Clear conversation history for a session. */
export function reset(sessionId) {
  histories.delete(sessionId);
}

/**
 * Local response stub — returns a canned reply.
 * Replace this body with a local inference engine if desired.
 *
 * @param {string}   sessionId    Unique key for this conversation
 * @param {string}   userMessage  The user's input
 * @param {object}   [opts]
 * @param {function} [opts.onChunk]  Called with each text chunk
 * @returns {Promise<string>}  The full response text
 */
export async function chat(sessionId, userMessage, { onChunk } = {}) {
  if (!histories.has(sessionId)) histories.set(sessionId, []);
  const history = histories.get(sessionId);

  history.push({ role: 'user', content: userMessage });

  const reply = '[Local mode: no inference engine configured. '
    + 'See docs/prompts.md for the system prompt.]';

  if (onChunk) onChunk(reply);

  history.push({ role: 'assistant', content: reply });

  while (history.length > MAX_HISTORY) {
    history.splice(0, 2);
  }

  return reply;
}
