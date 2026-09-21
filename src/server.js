import http from "node:http";
import { config } from "./config.js";
import { verifySignature } from "./meta.js";
import { handleWebhook } from "./agent.js";

function json(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

  if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
    return json(res, 200, {
      ok: true,
      service: "ragnar-agent",
      instagram: "@ragnarplay1",
      website: config.website
    });
  }

  if (req.method === "GET" && url.pathname === "/webhook") {
    const mode = url.searchParams.get("hub.mode");
    const token = url.searchParams.get("hub.verify_token");
    const challenge = url.searchParams.get("hub.challenge");

    if (mode === "subscribe" && token && token === config.verifyToken) {
      res.writeHead(200, { "content-type": "text/plain" });
      return res.end(challenge || "");
    }
    return json(res, 403, { ok: false, error: "verification_failed" });
  }

  if (req.method === "POST" && url.pathname === "/webhook") {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks);

    if (!verifySignature(rawBody, req.headers["x-hub-signature-256"])) {
      return json(res, 401, { ok: false, error: "invalid_signature" });
    }

    let payload;
    try {
      payload = JSON.parse(rawBody.toString("utf8") || "{}");
    } catch {
      return json(res, 400, { ok: false, error: "invalid_json" });
    }

    const results = await handleWebhook(payload);
    return json(res, 200, { ok: true, results });
  }

  return json(res, 404, { ok: false, error: "not_found" });
});

server.listen(config.port, "0.0.0.0", () => {
  console.log(`Ragnar agent listening on port ${config.port}`);
});
