#!/usr/bin/env node
import { completeSimple, getModel } from "@earendil-works/pi-ai";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function extractJson(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    // Continue with a conservative fenced/embedded object extraction.
  }
  const withoutFence = trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
  try {
    return JSON.parse(withoutFence);
  } catch {
    // Continue.
  }
  const start = withoutFence.indexOf("{");
  const end = withoutFence.lastIndexOf("}");
  if (start >= 0 && end > start) {
    return JSON.parse(withoutFence.slice(start, end + 1));
  }
  return null;
}

function textFromAssistant(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content.trim();
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((block) => block && (block.type === "text" || block.type === "output_text" || typeof block.text === "string"))
    .map((block) => block.text || block.content || "")
    .join("")
    .trim();
}

async function main() {
  const payload = JSON.parse((await readStdin()) || "{}");
  const provider = String(payload.provider || "").trim();
  const modelId = String(payload.model || "").trim();
  const model = getModel(provider, modelId);
  if (!model) {
    console.log(JSON.stringify({
      ok: false,
      error_code: "pi_model_not_found",
      message: `pi-ai model not found: ${provider}/${modelId}`,
    }));
    return 0;
  }
  const prompt = String(payload.prompt || payload.message || "Return a JSON object.");
  const systemPrompt = String(payload.systemPrompt || "Return only valid JSON. Do not include markdown fences.");
  const response = await completeSimple(
    model,
    {
      systemPrompt,
      messages: [{ role: "user", content: prompt, timestamp: Date.now() }],
    },
    {
      temperature: Number.isFinite(payload.temperature) ? payload.temperature : 0.2,
      timeoutMs: Number.isFinite(payload.timeoutMs) ? payload.timeoutMs : 30000,
      maxRetries: 0,
    },
  );
  if (response && (response.stopReason === "error" || response.stopReason === "aborted" || response.errorMessage)) {
    console.log(JSON.stringify({
      ok: false,
      provider,
      model: modelId,
      api: model.api,
      error_code: response.stopReason === "aborted" ? "pi_request_aborted" : "pi_provider_error",
      message: response.errorMessage || `pi-ai generation stopped with ${response.stopReason || "error"}`,
    }));
    return;
  }
  const text = textFromAssistant(response);
  const parsed = extractJson(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    console.log(JSON.stringify({
      ok: false,
      provider,
      model: modelId,
      error_code: "pi_non_json_response",
      message: "pi-ai response did not contain a JSON object",
      text,
    }));
    return 0;
  }
  console.log(JSON.stringify({
    ok: true,
    provider,
    model: modelId,
    api: model.api,
    responseModel: response.responseModel || response.model,
    json: parsed,
  }));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
