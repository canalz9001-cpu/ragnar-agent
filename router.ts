import { timingSafeEqual } from "node:crypto";
import { Router } from "express";
import { env } from "../config/env.js";
import { db } from "../database/db.js";

export const odinRouter = Router();

function secureEqual(a: string, b: string) {
  const left = Buffer.from(a); const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function basicAuthorized(header: string | undefined) {
  if (!env.ODIN_ADMIN_PASSWORD || !header?.startsWith("Basic ")) return false;
  try {
    const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
    const [, password = ""] = decoded.split(":", 2);
    return secureEqual(password, env.ODIN_ADMIN_PASSWORD);
  } catch { return false; }
}

odinRouter.get("/", async (req, res) => {
  if (!basicAuthorized(req.headers.authorization)) {
    res.setHeader("WWW-Authenticate", 'Basic realm="Odin Leads"');
    return res.status(401).send("Autenticação necessária");
  }
  const result = await db.query(
    `SELECT instagram_username,brand,intent,temperature,score,stage,needs_human,
            trigger_keyword,updated_at
     FROM leads ORDER BY needs_human DESC,score DESC,updated_at DESC LIMIT 200`
  );
  const counts = result.rows.reduce((acc: Record<string, number>, row: any) => {
    acc[row.temperature] = (acc[row.temperature] || 0) + 1; return acc;
  }, {});
  const rows = result.rows.map((row: any) => `<tr><td>${escapeHtml(row.instagram_username || "Sem @")}</td><td>${escapeHtml(row.brand)}</td><td>${escapeHtml(row.intent || "—")}</td><td>${escapeHtml(row.temperature)}</td><td>${row.score}</td><td>${escapeHtml(row.stage)}</td><td>${new Date(row.updated_at).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}</td></tr>`).join("");
  res.type("html").send(`<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Odin Leads</title><style>body{background:#071018;color:#eef3f7;font:15px Arial;margin:0;padding:24px}.wrap{max-width:1200px;margin:auto}h1{font-size:30px}.cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:#101c27;border:1px solid #263746;border-radius:14px;padding:16px;min-width:160px}.n{font-size:28px;font-weight:800;color:#e22d3e}table{width:100%;border-collapse:collapse;margin-top:20px;background:#101c27}th,td{text-align:left;padding:12px;border-bottom:1px solid #263746}th{color:#9fb0bf}@media(max-width:800px){table{font-size:12px}th,td{padding:7px}}</style><div class="wrap"><h1>ODIN · Central de Leads</h1><div class="cards"><div class="card"><div class="n">${result.rowCount}</div>Total</div><div class="card"><div class="n">${counts.hot || 0}</div>Quentes</div><div class="card"><div class="n">${counts.warm || 0}</div>Mornos</div><div class="card"><div class="n">${counts.cold || 0}</div>Frios</div></div><table><thead><tr><th>Instagram</th><th>Marca</th><th>Interesse</th><th>Temperatura</th><th>Pontos</th><th>Etapa</th><th>Atualizado</th></tr></thead><tbody>${rows || '<tr><td colspan="7">Nenhum lead ainda.</td></tr>'}</tbody></table></div></html>`);
});

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]!));
}
