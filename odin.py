"""Odin: qualificação comercial e funil de leads do Ragnar One."""
import html
import os
import re
import time
import urllib.parse


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def enabled():
    return _env("ODIN_ENABLED", "true").lower() == "true"


def ensure_schema(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS odin_leads (
            instagram_user_id TEXT PRIMARY KEY,
            instagram_username TEXT,
            source TEXT NOT NULL DEFAULT 'instagram',
            trigger_keyword TEXT,
            intent TEXT,
            interest TEXT,
            temperature TEXT NOT NULL DEFAULT 'cold',
            score INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT 'new',
            needs_human INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            last_message TEXT,
            created REAL NOT NULL,
            updated REAL NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS odin_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instagram_user_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            body TEXT NOT NULL,
            created REAL NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_odin_queue "
        "ON odin_leads(needs_human, temperature, score, updated)"
    )


def _normalize(text):
    value = (text or "").lower()
    value = (
        value.replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
        .replace("é", "e").replace("ê", "e").replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o")
        .replace("ú", "u").replace("ç", "c")
    )
    return re.sub(r"\s+", " ", value).strip()

AUTOMATION_MARKERS = (
    "sou a assistente virtual",
    "sou o assistente virtual",
    "assistente virtual",
    "atendimento automatico",
    "atendimento automatizado",
    "responda 1",
    "responda 2",
    "responda 3",
    "digite 1",
    "digite 2",
    "escolha uma opcao",
    "escolha uma das opcoes",
    "selecione uma opcao",
    "para eu te ajudar rapido",
    "para continuar escolha",
    "menu de atendimento",
    "falar no whatsapp",
    "acesse nosso site",
)


def _loop_text(text):
    value = _normalize(text)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\b\d{2,}\b", "#", value)
    value = re.sub(r"[^a-z0-9# ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _automation_marker_score(text):
    value = _loop_text(text)
    return sum(1 for marker in AUTOMATION_MARKERS if marker in value)


def automation_loop_reason(connection, user_id, inbound_text, window_seconds=600):
    """Detect likely bot-to-bot loops without relying on a provider bot flag.

    Returns a short reason string when the reply should be suppressed, otherwise "".
    """
    if not user_id:
        return ""
    ensure_schema(connection)
    cutoff = time.time() - max(60, int(window_seconds))
    rows = connection.execute(
        """SELECT direction,body,created
           FROM odin_messages
           WHERE instagram_user_id=? AND created>=?
           ORDER BY created DESC LIMIT 20""",
        (str(user_id), cutoff),
    ).fetchall()
    inbound = [row for row in rows if row[0] == "inbound"]
    outbound = [row for row in rows if row[0] == "outbound"]
    normalized = _loop_text(inbound_text)
    same_inbound = sum(1 for row in inbound if _loop_text(row[1]) == normalized)
    marker_score = _automation_marker_score(inbound_text)

    if marker_score >= 2 and outbound:
        return "automation_signature"
    if same_inbound >= 2 and len(outbound) >= 2:
        return "repeated_inbound"
    if len(inbound) >= 5 and len(outbound) >= 5:
        return "rapid_ping_pong"
    automated_inbound = sum(1 for row in inbound if _automation_marker_score(row[1]) >= 1)
    if automated_inbound >= 3 and len(outbound) >= 3:
        return "automation_burst"
    return ""




def _classify(text):
    t = _normalize(text)
    human = any(x in t for x in (
        "falar com atendente", "falar com uma pessoa", "atendente humano",
        "reembolso", "cobranca", "nao funciona", "fora do ar", "erro de login"
    ))
    hot = human or any(x in t for x in (
        "quero assinar", "quero comprar", "como pago", "como pagar",
        "forma de pagamento", "manda o pix", "fechar", "contratar"
    ))
    warm = any(x in t for x in (
        "quanto custa", "qual valor", "preco", "plano", "teste",
        "testar", "tenho interesse", "quero", "revenda", "revendedor"
    ))
    if hot:
        return "hot", True
    if warm:
        return "warm", False
    return "cold", False


def _score_delta(text):
    t = _normalize(text)
    score = 0
    for phrase, points in (
        ("quero", 12), ("teste", 14), ("testar", 14), ("preco", 10),
        ("valor", 10), ("plano", 8), ("assinar", 25), ("comprar", 25),
        ("pagar", 25), ("pix", 30), ("revenda", 15), ("revendedor", 15),
        ("agora", 8), ("hoje", 5)
    ):
        if phrase in t:
            score += points
    return min(score, 50)


def _intent(text):
    t = _normalize(text)
    if any(x in t for x in ("revenda", "revendedor", "revender", "painel")):
        return "revenda"
    if any(x in t for x in ("uso proprio", "cliente", "assinar", "filme", "serie", "futebol", "tv")):
        return "assinatura"
    if any(x in t for x in ("suporte", "problema", "nao funciona", "travando", "fora do ar")):
        return "suporte"
    return None


def capture_lead(connection, user_id, username, body, trigger_keyword=None, source="instagram"):
    if not enabled() or not user_id:
        return None
    ensure_schema(connection)
    now = time.time()
    temperature, needs_human = _classify(body)
    intent = _intent(body)
    delta = _score_delta(body)
    row = connection.execute(
        "SELECT temperature,score,stage,needs_human,intent FROM odin_leads WHERE instagram_user_id=?",
        (str(user_id),),
    ).fetchone()
    rank = {"cold": 0, "warm": 1, "hot": 2}
    if row:
        old_temp, old_score, stage, old_human, old_intent = row
        final_temp = temperature if rank[temperature] > rank.get(old_temp, 0) else old_temp
        connection.execute(
            """UPDATE odin_leads SET
               instagram_username=COALESCE(NULLIF(?,''),instagram_username),
               trigger_keyword=COALESCE(?,trigger_keyword),
               intent=COALESCE(?,intent),
               temperature=?,
               score=MIN(100,score+?),
               needs_human=?,
               last_message=?,
               updated=?
               WHERE instagram_user_id=?""",
            (
                username or "", trigger_keyword, intent, final_temp, delta,
                1 if (needs_human or old_human) else 0, body or "", now, str(user_id),
            ),
        )
    else:
        initial_score = max(10 if trigger_keyword else 0, delta)
        connection.execute(
            """INSERT INTO odin_leads(
               instagram_user_id,instagram_username,source,trigger_keyword,intent,
               temperature,score,stage,needs_human,last_message,created,updated
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(user_id), username or None, source, trigger_keyword, intent,
                temperature if trigger_keyword is None else ("warm" if temperature == "cold" else temperature),
                initial_score, "new", 1 if needs_human else 0, body or "", now, now,
            ),
        )
    connection.execute(
        "INSERT INTO odin_messages(instagram_user_id,direction,body,created) VALUES(?,?,?,?)",
        (str(user_id), "inbound", body or "", now),
    )
    return get_lead(connection, user_id)


def get_lead(connection, user_id):
    ensure_schema(connection)
    row = connection.execute(
        """SELECT instagram_user_id,instagram_username,intent,temperature,score,stage,
                  needs_human,last_message FROM odin_leads WHERE instagram_user_id=?""",
        (str(user_id),),
    ).fetchone()
    if not row:
        return None
    return {
        "user_id": row[0], "username": row[1], "intent": row[2],
        "temperature": row[3], "score": int(row[4] or 0), "stage": row[5],
        "needs_human": bool(row[6]), "last_message": row[7] or "",
    }


def _set_state(connection, user_id, stage=None, intent=None, score_delta=0, temperature=None, needs_human=None):
    fields = ["score=MIN(100,MAX(0,score+?))", "updated=?"]
    values = [int(score_delta), time.time()]
    if stage is not None:
        fields.append("stage=?"); values.append(stage)
    if intent is not None:
        fields.append("intent=?"); values.append(intent)
    if temperature is not None:
        fields.append("temperature=?"); values.append(temperature)
    if needs_human is not None:
        fields.append("needs_human=?"); values.append(1 if needs_human else 0)
    values.append(str(user_id))
    connection.execute("UPDATE odin_leads SET " + ",".join(fields) + " WHERE instagram_user_id=?", values)


def qualification_reply(connection, user_id, username, body, website, whatsapp_url):
    lead = capture_lead(connection, user_id, username, body, source="direct")
    if not lead:
        return "Como posso te ajudar?"

    loop_reason = automation_loop_reason(connection, user_id, body)
    if loop_reason:
        return None

    t = _normalize(body)
    intent = lead.get("intent")
    stage = lead.get("stage") or "new"

    if lead.get("needs_human") or intent == "suporte":
        _set_state(connection, user_id, stage="human_handoff", score_delta=15, temperature="hot", needs_human=True)
        return (
            "Vou te encaminhar para atendimento humano para resolver isso mais rápido. "
            "Fale com nossa equipe pelo WhatsApp: " + whatsapp_url
        )

    if any(x in t for x in ("quero assinar", "quero comprar", "como pago", "como pagar", "pix", "fechar", "contratar")):
        _set_state(connection, user_id, stage="closing", score_delta=25, temperature="hot", needs_human=True)
        return (
            "Perfeito. Você já está na etapa de contratação. "
            "Para concluir, fale com nossa equipe pelo WhatsApp: " + whatsapp_url
        )

    if any(x in t for x in ("teste", "testar")):
        _set_state(connection, user_id, stage="trial_interest", score_delta=18, temperature="hot")
        return (
            "Ótimo. O teste é uma boa forma de conhecer a experiência antes de decidir. "
            "Você pode solicitar pelo site " + website +
            " ou falar direto com nossa equipe: " + whatsapp_url
        )

    if intent == "revenda" or any(x in t for x in ("revenda", "revendedor", "revender", "painel")):
        if stage in ("new", "intent"):
            _set_state(connection, user_id, stage="reseller_experience", intent="revenda", score_delta=12, temperature="warm")
            return "Entendi. Você já trabalha com revenda ou está começando agora?"
        if any(x in t for x in ("ja trabalho", "já trabalho", "tenho clientes", "revendo", "revendedor")):
            _set_state(connection, user_id, stage="reseller_pain", intent="revenda", score_delta=15, temperature="hot")
            return "Boa. Hoje o que mais te atrapalha: travamentos, suporte que demora ou fornecedor que some?"
        _set_state(connection, user_id, stage="reseller_offer", intent="revenda", score_delta=8, temperature="warm")
        return (
            "O Ragnar One pode te mostrar as opções para revenda e suporte. "
            "Quer conhecer os painéis disponíveis ou falar direto com a equipe? " + whatsapp_url
        )

    if any(x in t for x in ("preco", "valor", "plano", "quanto custa")):
        _set_state(connection, user_id, stage="offer_interest", intent="assinatura", score_delta=12, temperature="warm")
        return (
            "Claro. Para eu te direcionar melhor: você quer assistir em quantos dispositivos ao mesmo tempo? "
            "Você também pode consultar as opções no site: " + website
        )

    if stage == "new":
        _set_state(connection, user_id, stage="intent", score_delta=5)
        return "Pra eu te ajudar melhor: você procura para uso próprio ou quer trabalhar com revenda?"

    if stage == "intent":
        if any(x in t for x in ("uso proprio", "assistir", "cliente", "casa", "familia")):
            _set_state(connection, user_id, stage="pain", intent="assinatura", score_delta=10, temperature="warm")
            return "Perfeito. O que mais te incomoda hoje: travamentos, pouco conteúdo ou suporte que não responde?"
        return "Você está buscando uma assinatura para assistir ou uma opção para revender?"

    if stage == "pain":
        _set_state(connection, user_id, stage="solution", score_delta=8, temperature="warm")
        return (
            "Entendi. A proposta do Ragnar One é justamente entregar uma experiência mais estável e suporte quando você precisa. "
            "Quer testar primeiro ou conhecer as opções disponíveis?"
        )

    if stage in ("solution", "offer_interest"):
        _set_state(connection, user_id, stage="trial_or_close", score_delta=8, temperature="warm")
        return "Você prefere testar primeiro ou já quer falar com a equipe para contratar?"

    return (
        "Posso te ajudar com teste, planos, revenda ou suporte. "
        "Se preferir falar direto com a equipe: " + whatsapp_url
    )


def record_outbound(connection, user_id, body):
    if not user_id:
        return
    ensure_schema(connection)
    connection.execute(
        "INSERT INTO odin_messages(instagram_user_id,direction,body,created) VALUES(?,?,?,?)",
        (str(user_id), "outbound", body or "", time.time()),
    )


def nexus_leads_snapshot(connection):
    ensure_schema(connection)
    rows = connection.execute(
        """SELECT instagram_user_id,instagram_username,intent,temperature,score,stage,
                  needs_human,trigger_keyword,last_message,updated
           FROM odin_leads
           ORDER BY needs_human DESC,
                    CASE temperature WHEN 'hot' THEN 3 WHEN 'warm' THEN 2 ELSE 1 END DESC,
                    score DESC,updated DESC LIMIT 500"""
    ).fetchall()
    leads = []
    summary = {"total": 0, "hot": 0, "warm": 0, "cold": 0, "needsHuman": 0}
    for row in rows:
        lead = {
            "instagramUserId": str(row[0] or ""),
            "instagramUsername": str(row[1] or ""),
            "intent": str(row[2] or ""),
            "temperature": str(row[3] or "cold"),
            "score": int(row[4] or 0),
            "stage": str(row[5] or "new"),
            "needsHuman": bool(row[6]),
            "triggerKeyword": str(row[7] or ""),
            "lastMessage": str(row[8] or ""),
            "updatedAt": float(row[9] or 0),
        }
        leads.append(lead)
        summary["total"] += 1
        temp = lead["temperature"] if lead["temperature"] in ("hot", "warm", "cold") else "cold"
        summary[temp] += 1
        if lead["needsHuman"]:
            summary["needsHuman"] += 1
    return {"ok": True, "summary": summary, "leads": leads}


def _authorized(environ):
    password = _env("ODIN_ADMIN_PASSWORD") or _env("PANEL_PASSWORD")
    if not password:
        return False
    header = environ.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return False
    try:
        import base64
        decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
        supplied = decoded.split(":", 1)[1] if ":" in decoded else decoded
        import hmac
        return hmac.compare_digest(supplied, password)
    except Exception:
        return False


def dashboard(environ, start_response, connection):
    if not _authorized(environ):
        body = b"Autenticacao necessaria"
        start_response("401 Unauthorized", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("WWW-Authenticate", 'Basic realm="Odin Leads"'),
        ])
        return [body]

    ensure_schema(connection)
    rows = connection.execute(
        """SELECT instagram_username,instagram_user_id,intent,temperature,score,stage,
                  needs_human,trigger_keyword,updated
           FROM odin_leads
           ORDER BY needs_human DESC,
                    CASE temperature WHEN 'hot' THEN 3 WHEN 'warm' THEN 2 ELSE 1 END DESC,
                    score DESC,updated DESC LIMIT 200"""
    ).fetchall()
    counts = {"hot": 0, "warm": 0, "cold": 0}
    for row in rows:
        counts[row[3]] = counts.get(row[3], 0) + 1

    def esc(v):
        return html.escape(str(v if v not in (None, "") else "—"))

    table = "".join(
        "<tr>"
        f"<td>{esc(r[0] or ('ID '+str(r[1])[-6:]))}</td>"
        f"<td>{esc(r[2])}</td><td><b>{esc(r[3])}</b></td>"
        f"<td>{esc(r[4])}</td><td>{esc(r[5])}</td>"
        f"<td>{'SIM' if r[6] else 'não'}</td>"
        f"<td>{esc(r[7])}</td>"
        f"<td>{time.strftime('%d/%m %H:%M', time.localtime(r[8]))}</td>"
        "</tr>"
        for r in rows
    )
    page = f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Odin · Ragnar One</title>
<style>
body{{background:#06110a;color:#eef8f1;font:15px Arial;margin:0;padding:24px}}
.wrap{{max-width:1200px;margin:auto}}h1{{font-size:30px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{background:#0d2015;border:1px solid #244b32;border-radius:14px;padding:16px;min-width:150px}}
.n{{font-size:28px;font-weight:900;color:#19c563}}table{{width:100%;border-collapse:collapse;margin-top:20px;background:#0d2015}}
th,td{{text-align:left;padding:11px;border-bottom:1px solid #244b32}}th{{color:#9fc8ad}}
.hot{{color:#53f690}}@media(max-width:800px){{table{{font-size:11px}}th,td{{padding:6px}}}}
</style><div class="wrap"><h1>ODIN · Central de Leads Ragnar</h1>
<div class="cards"><div class="card"><div class="n">{len(rows)}</div>Total</div>
<div class="card"><div class="n">{counts.get('hot',0)}</div>Quentes</div>
<div class="card"><div class="n">{counts.get('warm',0)}</div>Mornos</div>
<div class="card"><div class="n">{counts.get('cold',0)}</div>Frios</div></div>
<table><thead><tr><th>Instagram</th><th>Interesse</th><th>Temp.</th><th>Pontos</th><th>Etapa</th><th>Humano</th><th>Gatilho</th><th>Atualizado</th></tr></thead>
<tbody>{table or '<tr><td colspan="8">Nenhum lead ainda.</td></tr>'}</tbody></table></div></html>"""
    raw = page.encode("utf-8")
    start_response("200 OK", [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(raw))),
        ("Cache-Control", "no-store"),
    ])
    return [raw]
