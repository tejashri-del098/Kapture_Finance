import { readFile, writeFile } from "node:fs/promises";

const template = JSON.parse(await readFile(new URL("../vapi-assistant-config.json", import.meta.url), "utf8"));
const schemas = JSON.parse(await readFile(new URL("../tool-schemas.json", import.meta.url), "utf8"));
const promptMarkdown = await readFile(new URL("../system-prompt.md", import.meta.url), "utf8");

// Markdown headings make the prompt easier to review in the repository and are accepted as text by Vapi.
template.model.messages = [{ role: "system", content: promptMarkdown }];
template.model.functions = schemas.functions;

await writeFile(
  new URL("../vapi-assistant-payload.json", import.meta.url),
  `${JSON.stringify(template, null, 2)}\n`
);

console.info("Created vapi-assistant-payload.json. Replace YOUR-PUBLIC-HTTPS-URL before importing it into Vapi.");
