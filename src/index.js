import 'dotenv/config';
import readline from 'readline';
import { chat, reset } from './claude.js';

// ---------------------------------------------------------------------------
// Terminal formatting
// ---------------------------------------------------------------------------

const c = {
  reset:  '\x1b[0m',
  bold:   '\x1b[1m',
  dim:    '\x1b[2m',
  cyan:   '\x1b[36m',
  green:  '\x1b[32m',
  yellow: '\x1b[33m',
  gray:   '\x1b[90m',
  white:  '\x1b[97m',
};

const out = (text) => process.stdout.write(text);
const line = (text = '') => console.log(text);

// ---------------------------------------------------------------------------
// Startup checks
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

function banner() {
  line();
  line(`  ${c.bold}${c.white}aicord${c.reset}  ${c.dim}software engineering · customs & trade compliance${c.reset}`);
  line(`  ${c.gray}Running in local mode${c.reset}`);
  line();
  line(`  ${c.gray}Commands: /help  /reset  /exit${c.reset}`);
  line();
}

function printHelp() {
  line();
  line(`  ${c.dim}/reset   Clear conversation history and start fresh`);
  line(`  /help    Show this message`);
  line(`  /exit    Quit  (Ctrl+C also works)${c.reset}`);
  line();
}

function separator() {
  line(`  ${c.gray}${'─'.repeat(56)}${c.reset}`);
}

// ---------------------------------------------------------------------------
// Main REPL
// ---------------------------------------------------------------------------

async function main() {
  banner();

  const SESSION = 'cli';

  const rl = readline.createInterface({
    input:  process.stdin,
    output: process.stdout,
  });

  rl.on('close', () => {
    line(`\n  ${c.dim}Goodbye.${c.reset}\n`);
    process.exit(0);
  });

  const prompt = () => {
    rl.question(`${c.cyan}${c.bold}  You: ${c.reset}`, async (raw) => {
      const input = raw.trim();

      if (!input) {
        prompt();
        return;
      }

      // Built-in commands
      if (input === '/exit' || input === '/quit') {
        rl.close();
        return;
      }

      if (input === '/reset') {
        reset(SESSION);
        line();
        line(`  ${c.dim}Conversation cleared.${c.reset}`);
        line();
        prompt();
        return;
      }

      if (input === '/help') {
        printHelp();
        prompt();
        return;
      }

      // Send to the assistant
      line();
      out(`  ${c.bold}Assistant:${c.reset} `);

      try {
        await chat(SESSION, input, {
          onChunk: (text) => out(text),
        });
      } catch (err) {
        line();
        line(`  ${c.yellow}Error: ${err.message}${c.reset}`);
      }

      line('\n');
      separator();
      line();
      prompt();
    });
  };

  prompt();
}

main();
