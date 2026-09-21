import { config } from "./config.js";
import { sendPrivateReply, sendInstagramMessage } from "./meta.js";

const seenEvents = new Set();

function containsQuero(text = "") {
  return /(^|\W)quero($|\W)/i.test(text.normalize("NFD").replace(/[\u0300-\u036f]/g, ""));
}

function eventKey(parts) {
  return parts.filter(Boolean).join(":");
}

export async function handleWebhook(payload) {
  const results = [];

  for (const entry of payload?.entry || []) {
    for (const change of entry?.changes || []) {
      if (change?.field !== "comments") continue;

      const value = change.value || {};
      const commentId = value.id || value.comment_id;
      const text = value.text || "";
      if (!commentId || !containsQuero(text)) continue;

      const key = eventKey(["comment", commentId]);
      if (seenEvents.has(key)) {
        results.push({ type: "comment", commentId, status: "duplicate_ignored" });
        continue;
      }
      seenEvents.add(key);

      try {
        const response = await sendPrivateReply(commentId, config.greeting);
        results.push({ type: "comment", commentId, status: "private_reply_attempted", response });
      } catch (error) {
        results.push({ type: "comment", commentId, status: "error", error: error.message });
      }
    }

    for (const messaging of entry?.messaging || []) {
      const senderId = messaging?.sender?.id;
      const messageId = messaging?.message?.mid;
      const text = messaging?.message?.text || "";

      if (!senderId || !messageId || !text || messaging?.message?.is_echo) continue;

      const key = eventKey(["dm", messageId]);
      if (seenEvents.has(key)) {
        results.push({ type: "dm", messageId, status: "duplicate_ignored" });
        continue;
      }
      seenEvents.add(key);

      const lower = text.toLowerCase();
      let reply = "Posso te ajudar com planos, teste, suporte ou falar com nossa equipe pelo WhatsApp: " + config.whatsappUrl;

      if (/(preço|preco|valor|plano)/i.test(lower)) {
        reply = "Temos planos a partir de R$ 25,00. Você também pode ver as opções no site: " + config.website;
      } else if (/(teste|testar)/i.test(lower)) {
        reply = "Você pode solicitar seu teste grátis pelo site: " + config.website + " ou falar com nossa equipe pelo WhatsApp: " + config.whatsappUrl;
      } else if (/(suporte|problema|reclama|cancel|pagamento)/i.test(lower)) {
        reply = "Vou te direcionar para atendimento humano. Fale com nossa equipe pelo WhatsApp: " + config.whatsappUrl;
      }

      try {
        const response = await sendInstagramMessage(senderId, reply);
        results.push({ type: "dm", messageId, status: "reply_attempted", response });
      } catch (error) {
        results.push({ type: "dm", messageId, status: "error", error: error.message });
      }
    }
  }

  return results;
}
