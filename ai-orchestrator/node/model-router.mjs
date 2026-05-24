import { readFileSync } from "node:fs";

export function chooseModel(packet = {}, env = process.env) {
  const cheap = packet.gptCheapModel || env.GPT_CHEAP_MODEL || "gpt-5.4-nano";
  const normal = packet.gptDefaultModel || env.GPT_DEFAULT_MODEL || "gpt-5.4-mini";
  const strategic = packet.gptStrategicModel || env.GPT_STRATEGIC_MODEL || "gpt-5.5";

  const text = [
    packet.text,
    packet.laneStatus,
    packet.failureLogTail,
    packet.gitDiffNames,
    packet.triggerReason,
    packet.taskQueue,
    packet.roadmapBacklog,
    packet.blockerAnalysis,
    packet.venueExpansionPlan,
    packet.latestClaudeReview,
    packet.latestCodexSummary
  ].filter(Boolean).join("\n").toLowerCase();

  const failureCount = Number(packet.failureCount || 0);
  const blocked = /\bblocked\b/.test(text);
  const repeatedFailure = failureCount > 1 || /retry failed|context_length_exceeded|stream disconnected|rate limit|econnreset|etimedout/.test(text);
  const architecture = /architecture|cross-lane|strategy change|strategic|disagree|disagreement/.test(text);
  const risky = /evaluator|settlement|slippage|fee|gas|paper_candidate|paper-candidate|same_payoff|matching|matcher|execution|live|risk|trust|graph-to-relative/.test(text);
  const highProfitConflict = /conflicting high-profit|profit path conflict|venue expansion|paper candidate appeared/.test(text);

  if (blocked || repeatedFailure || architecture || highProfitConflict || (risky && /claude|review|before commit/.test(text))) {
    return { model: strategic, reason: "Strategic escalation: blocked/repeated/risk-sensitive planning signal detected." };
  }

  if (/command extraction|classification|log tail|tiny summary|summarize|backlog extraction/.test(text)) {
    return { model: cheap, reason: "Cheap route: command/log classification, tiny summary, or backlog extraction." };
  }

  return { model: normal, reason: "Default route: routine lane prompt generation." };
}

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { data += chunk; });
    process.stdin.on("end", () => resolve(data));
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  let input = "";
  if (process.argv[2]) {
    input = readFileSync(process.argv[2], "utf8");
  } else {
    input = await readStdin();
  }

  const packet = input.trim() ? JSON.parse(input) : {};
  process.stdout.write(`${JSON.stringify(chooseModel(packet), null, 2)}\n`);
}
