import OpenAI from "openai";

const client = new OpenAI();

const response = await client.responses.create({
  model: "gpt-5.4-mini",
  input: "Reply with exactly: API works"
});

console.log(response.output_text);