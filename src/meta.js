import crypto from "node:crypto";
import { config } from "./config.js";

export function verifySignature(rawBody, signatureHeader) {
  if (!config.metaAppSecret) return true;
  if (!signatureHeader?.startsWith("sha256=")) return false;
  const expected = "sha256=" + crypto.createHmac("sha256", config.metaAppSecret).update(rawBody).digest("hex");
  try {
    return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signatureHeader));
  } catch {
    return false;
  }
}

export async function sendPrivateReply(commentId, message) {
  if (!config.instagramAccessToken) {
    return { ok: false, skipped: true, reason: "missing_access_token" };
  }

  const url = new URL(`https://graph.facebook.com/${config.graphVersion}/${commentId}/private_replies`);
  url.searchParams.set("access_token", config.instagramAccessToken);

  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message })
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`Meta private reply failed: ${response.status} ${JSON.stringify(data)}`);
  }
  return data;
}

export async function sendInstagramMessage(recipientId, text) {
  if (!config.instagramAccessToken || !config.instagramAccountId) {
    return { ok: false, skipped: true, reason: "missing_instagram_credentials" };
  }

  const url = new URL(`https://graph.facebook.com/${config.graphVersion}/${config.instagramAccountId}/messages`);
  url.searchParams.set("access_token", config.instagramAccessToken);

  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      recipient: { id: recipientId },
      message: { text }
    })
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`Meta message failed: ${response.status} ${JSON.stringify(data)}`);
  }
  return data;
}
