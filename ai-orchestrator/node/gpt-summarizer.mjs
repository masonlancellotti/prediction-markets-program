import { readFileSync, writeFileSync } from "node:fs";

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

async function callOpenAI(apiKey, model, input) {
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${apiKey}`
    },
    body: JSON.stringify({
      model,
      input: `Summarize this log tail compactly. Preserve errors, paths, exit codes, and next actions. Do not invent success.\n\n${input}`
    })
  });

  const body = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI API error ${response.status}: ${body.slice(0, 2000)}`);
  }
  const json = JSON.parse(body);
  return json.output_text || (json.output || []).flatMap((item) => item.content || []).map((content) => content.text || "").join("\n");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = parseArgs(process.argv);
  const inputPath = args.input;
  const outputPath = args.output;
  const model = args.model || process.env.GPT_CHEAP_MODEL || "gpt-5.4-nano";

  if (!inputPath) {
    throw new Error("Missing --input path.");
  }
  if (!process.env.OPENAI_API_KEY) {
    throw new Error("OPENAI_API_KEY is not set.");
  }

  const input = readFileSync(inputPath, "utf8");
  const output = await callOpenAI(process.env.OPENAI_API_KEY, model, input);
  if (outputPath) {
    writeFileSync(outputPath, output, "utf8");
  } else {
    process.stdout.write(output);
  }
}
