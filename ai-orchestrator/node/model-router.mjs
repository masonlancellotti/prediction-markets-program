import { readFileSync } from "node:fs";

export function chooseModel(packet = {}, env = process.env) {
  const cheap = packet.gptCheapModel || env.GPT_CHEAP_MODEL || "gpt-5.4-nano";
  const normal = packet.gptDefaultModel || env.GPT_DEFAULT_MODEL || "gpt-5.4-mini";
  const strategic = packet.gptStrategicModel || env.GPT_STRATEGIC_MODEL || "gpt-5.5";

  const allText = [
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

  const failureCountFromText = Number((allText.match(/failure count:\s*(\d+)/) || [])[1] || 0);
  const failureCount = Number(packet.failureCount || failureCountFromText || 0);
  const laneStatusText = String(packet.laneStatus || "").toLowerCase();

  // Plain-text packets include stable guardrails, so generic words like
  // "risk", "matching", "same_payoff", "review", and "evaluator" are
  // deliberately not escalation triggers by themselves.
  const blocked =
    /status:\s*blocked/.test(laneStatusText) ||
    /## lane_status\.md[\s\S]{0,800}status:\s*blocked/.test(allText) ||
    /lane is blocked|blocked lane recovery/.test(String(packet.triggerReason || "").toLowerCase());
  const repeatedFailure =
    failureCount > 1 ||
    /retry failed|same task failed twice|repeated failure|context_length_exceeded|stream disconnected|rate limit|econnreset|etimedout/.test(allText);
  const explicitDisagreement = /claude\/codex disagreement|claude and codex disagree|codex and claude disagree|conflicting reviews/.test(allText);
  const architecture = /architecture planning|architecture change|cross-lane strategy change|cross lane strategy change|strategic direction change/.test(allText);
  const paperCandidate = /paper_candidate_found|paper candidate appeared|paper candidate found/.test(allText);
  const evaluatorGateChange = /evaluator gate change|change evaluator gate|weaken evaluator|weaken.*gate/.test(allText);
  const settlementTrustChange = /settlement trust change|trust new settlement|settlement normalization trust|change settlement trust/.test(allText);
  const feeModelChange = /fee\/slippage\/gas model change|fee model change|slippage model change|gas model change/.test(allText);
  const graphIntegration = /graph-to-relative integration|promote graph hints|graph hints.*paper candidate/.test(allText);
  const liveExecutionDesign = /live execution design|live trading design|order submission design|execution logic design/.test(allText);
  const highProfitConflict = /conflicting high-profit|profit path conflict/.test(allText);

  if (
    blocked ||
    repeatedFailure ||
    paperCandidate ||
    explicitDisagreement ||
    architecture ||
    evaluatorGateChange ||
    settlementTrustChange ||
    feeModelChange ||
    graphIntegration ||
    liveExecutionDesign ||
    highProfitConflict
  ) {
    return { model: strategic, reason: "Strategic escalation: blocked/repeated/risk-sensitive planning signal detected." };
  }

  if (/command extraction|classification|log tail|log clipping|tiny summary|summarize|backlog extraction/.test(allText)) {
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
