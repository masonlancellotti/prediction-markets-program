import { readFileSync, writeFileSync } from "node:fs";
import { chooseModel } from "./model-router.mjs";

export const MARKER_SECTIONS = [
  "UPDATED_PROGRAM_STATUS",
  "UPDATED_ACTIVE_GOALS",
  "UPDATED_NEXT_STEPS",
  "UPDATED_LANE_STATUS",
  "NEXT_CODEX_PROMPT",
  "SHORT_COMMANDS_JSONL",
  "LONG_COMMANDS_MD",
  "NEXT_ACTION_PACKET",
  "CLAUDE_REVIEW_NEEDED",
  "BLOCKED_REASON",
  "ROADMAP_BACKLOG_UPDATES",
  "USER_ACTION_REQUIRED",
  "TASK_QUEUE_UPDATES",
  "MODEL_USED",
  "REASONING_SUMMARY"
];

export function findMissingMarkers(text) {
  return MARKER_SECTIONS.filter((name) => {
    return !text.includes(`${name}_START`) || !text.includes(`${name}_END`);
  });
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith("--")) {
        args[key] = next;
        i += 1;
      } else {
        args[key] = true;
      }
    }
  }
  return args;
}

function buildSystemPrompt(packet, modelInfo) {
  return `You are the GPT prompter for a visible local AI orchestrator.

Use compact context. Do not execute commands. Do not ask Mason to paste command outputs manually; write command requests into the marked sections.

The API/GPT side owns TASK_QUEUE.json, ROADMAP_BACKLOG.json, and routine state updates. Codex is only a bounded worker. Claude is a sparse high-risk reviewer.

Task rules:
- Select only the first ready task for this lane from TASK_QUEUE.json unless that task is blocked, waiting_review, done, or stale.
- Include the selected task id in NEXT_CODEX_PROMPT and NEXT_ACTION_PACKET.
- Do not invent runnable tasks while TASK_QUEUE has a ready task for this lane.
- Never output a broad "continue previous work" task. Every task needs task id, file scope, tests, success criteria, and stop conditions.
- If NEXT_CODEX_PROMPT already has an active task and there is no new Codex summary, command result, review, or explicit GPT trigger, return UNCHANGED for NEXT_CODEX_PROMPT.
- If tests failed, produce a fix-tests task instead of a feature task.
- If a paper candidate appears, stop the lane and request Claude/human review.
- If changed files fall outside the selected task allowed_files, mark the lane BLOCKED.
- If untracked imported modules appear, request import hygiene instead of feature work.

Roadmap replenishment:
- Identify blockers, feature opportunities, missing APIs/venues/data sources, and repeated failure patterns from fresh outputs.
- Propose 1-5 bounded additions to ROADMAP_BACKLOG.json when useful.
- Promote at most one backlog item into TASK_QUEUE.json per cycle.
- Never overwrite existing ready tasks unless they are done, blocked, or stale.
- Write user-needed setup, approvals, account/API connection decisions, long commands, or paper-candidate inspection requests to USER_ACTION_REQUIRED.

Return exactly these marker sections:
UPDATED_PROGRAM_STATUS_START
UNCHANGED or replacement text
UPDATED_PROGRAM_STATUS_END
UPDATED_ACTIVE_GOALS_START
UNCHANGED or replacement text
UPDATED_ACTIVE_GOALS_END
UPDATED_NEXT_STEPS_START
UNCHANGED or replacement text
UPDATED_NEXT_STEPS_END
UPDATED_LANE_STATUS_START
UNCHANGED or replacement text
UPDATED_LANE_STATUS_END
NEXT_CODEX_PROMPT_START
exact next Codex prompt, one small task only
NEXT_CODEX_PROMPT_END
SHORT_COMMANDS_JSONL_START
zero or more JSONL safe short command requests
SHORT_COMMANDS_JSONL_END
LONG_COMMANDS_MD_START
zero or more long/manual command requests
LONG_COMMANDS_MD_END
NEXT_ACTION_PACKET_START
short user-facing lane packet
NEXT_ACTION_PACKET_END
CLAUDE_REVIEW_NEEDED_START
YES or NO, with one short reason
CLAUDE_REVIEW_NEEDED_END
BLOCKED_REASON_START
UNCHANGED or concise blocker that should pause the lane
BLOCKED_REASON_END
ROADMAP_BACKLOG_UPDATES_START
UNCHANGED or full replacement ROADMAP_BACKLOG.json
ROADMAP_BACKLOG_UPDATES_END
USER_ACTION_REQUIRED_START
UNCHANGED or markdown entries to append to USER_ACTION_REQUIRED.md
USER_ACTION_REQUIRED_END
TASK_QUEUE_UPDATES_START
UNCHANGED or full replacement TASK_QUEUE.json
TASK_QUEUE_UPDATES_END
MODEL_USED_START
${modelInfo.model}
MODEL_USED_END
REASONING_SUMMARY_START
brief non-sensitive routing/planning summary
REASONING_SUMMARY_END

Guardrails:
- Relative-value dislocations are not guaranteed edge.
- Do not confuse detected mismatch with tradable/profitable opportunity.
- No midpoint-fill assumptions.
- No profit claims from stale quotes.
- Sportsbook/reference odds are reference-only unless explicitly executable through approved APIs.
- No title similarity as settlement equivalence.
- Do not ignore fees, slippage, spread, posted size, quote age/freshness, settlement wording, deadline/timezone, or liquidity.
- Uncertain outputs stay WATCH or MANUAL_REVIEW.
- Graph hints are diagnostic-only and must not become paper candidates.
- Weather edge requires external observations/forecasts/settlement labels and conservative validation.
- No live trading/order/auth/account/private-key/signing/wallet/deploy/git push logic.
- If a task could create PAPER_CANDIDATE, weaken a gate, trust new settlement normalization, alter fee/slippage logic, or promote graph hints into executable signals, mark Claude review needed.

Safe short command schema, one JSON object per line:
{"id":"unique-stable-id","classification":"SAFE_SHORT_AUTO","cwd":"C:\\\\absolute\\\\lane\\\\path","command":"git status --short","reason":"why needed","expected_output":"what success looks like","timeout_seconds":120}

Router decision: ${modelInfo.reason}

Packet follows:
${packet.text}`;
}

async function callOpenAI({ apiKey, model, input }) {
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${apiKey}`
    },
    body: JSON.stringify({ model, input, temperature: 0.2 })
  });

  const body = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI API error ${response.status}: ${body.slice(0, 2000)}`);
  }

  const json = JSON.parse(body);
  if (typeof json.output_text === "string") {
    return json.output_text;
  }

  const parts = [];
  for (const item of json.output || []) {
    for (const content of item.content || []) {
      if (typeof content.text === "string") {
        parts.push(content.text);
      }
    }
  }
  return parts.join("\n");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = parseArgs(process.argv);
  const inputPath = args.input;
  const outputPath = args.output;

  if (!inputPath) {
    throw new Error("Missing --input packet path.");
  }

  const packetJson = JSON.parse(readFileSync(inputPath, "utf8"));
  const modelInfo = chooseModel(packetJson);
  const model = args.model || modelInfo.model;
  const apiKey = process.env.OPENAI_API_KEY;

  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is not set.");
  }

  const prompt = buildSystemPrompt(packetJson, { ...modelInfo, model });
  const output = await callOpenAI({ apiKey, model, input: prompt });
  const missing = findMissingMarkers(output);
  if (missing.length > 0) {
    throw new Error(`GPT output missing required markers: ${missing.join(", ")}`);
  }

  if (outputPath) {
    writeFileSync(outputPath, output, "utf8");
  } else {
    process.stdout.write(output);
  }
}
