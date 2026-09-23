"""Pesquisa tendências, aprende com o Instagram da Ragnar e melhora legendas."""
import json
import os
import re
import time
import urllib.error
import urllib.request


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _json_text(response):
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts)


def _parse_json(text):
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _openai(instructions, user_input, web_search=False):
    key = _env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY não configurada")
    body = {
        "model": _env("OPENAI_MODEL", "gpt-5.6-luna"),
        "instructions": instructions,
        "input": user_input,
    }
    if web_search:
        body["tools"] = [{"type": "web_search"}]
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "RagnarAgent/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            return _json_text(json.load(response))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail[:400]}")


def _ensure_table(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS content_intelligence ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "kind TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL)"
    )


def _latest(db_factory, kind, max_age):
    with db_factory() as connection:
        _ensure_table(connection)
        row = connection.execute(
            "SELECT payload FROM content_intelligence WHERE kind=? AND created>? "
            "ORDER BY created DESC LIMIT 1",
            (kind, time.time() - max_age),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _save(db_factory, kind, payload):
    with db_factory() as connection:
        _ensure_table(connection)
        connection.execute(
            "INSERT INTO content_intelligence(kind,created,payload) VALUES(?,?,?)",
            (kind, time.time(), json.dumps(payload, ensure_ascii=False)),
        )
        connection.execute(
            "DELETE FROM content_intelligence WHERE id NOT IN "
            "(SELECT id FROM content_intelligence ORDER BY created DESC LIMIT 100)"
        )


def fallback_research():
    return {
        "signals": [
            "O primeiro segundo precisa mostrar entretenimento, movimento ou curiosidade visual.",
            "Futebol, cinema em casa, maratona e escolha do que assistir geram identificação rápida.",
            "Emoção positiva e cenas desejáveis dão mais espaço para compartilhamento do que sofrimento literal.",
            "Variação de cenários e formatos ajuda a descobrir o que a própria audiência responde melhor.",
        ],
        "hooks": [
            "Hoje tem jogo. Sua tela está pronta?",
            "Seu sofá virou cinema.",
            "Filme, série ou futebol: qual vai ser hoje?",
            "Dê play no seu momento.",
        ],
        "formats": ["momento aspiracional", "pergunta de escolha", "surpresa visual", "humor leve", "energia de jogo"],
        "avoid": ["homem sofrendo", "pessoa triste ou desesperada", "antes triste/depois feliz", "promessa de zero travamento", "métricas inventadas", "copiar criativos"],
        "summary": "Gancho imediato, desejo, curiosidade, entretenimento visual forte e CTA simples para comentar QUERO.",
    }


def research_trends(db_factory):
    cached = _latest(db_factory, "research", 20 * 3600)
    if cached:
        return cached
    fallback = fallback_research()
    try:
        memory = _latest(db_factory, "audit", 14 * 86400) or {}
        result = _parse_json(_openai(
            """Você pesquisa conteúdo público recente para a Ragnar One no Brasil.
Pesquise sinais dos últimos 30 dias em Instagram e web sobre entretenimento, streaming, filmes, séries,
futebol ao vivo, descoberta de conteúdo e comportamento de audiência. Extraia mecanismos de atenção,
retenção, curiosidade, emoção positiva, comentários e compartilhamento; não copie posts, slogans ou identidade de terceiros.
Problemas como travamento, delay e suporte podem ser usados apenas como contexto quando realmente ajudarem o gancho.
Não recomende cenas de sofrimento, raiva, desespero ou comparação antes triste/depois feliz.
Não invente métricas, não prometa viralização e não recomende pirataria.
Retorne SOMENTE JSON válido com as chaves signals, hooks, formats, avoid e summary.""",
            "Crie aprendizados práticos para os próximos Reels da Ragnar One. "
            "Compare com este histórico próprio quando disponível:\n" +
            json.dumps(memory, ensure_ascii=False)[:7000],
            web_search=True,
        ))
        research = {
            "signals": [str(x) for x in result.get("signals", [])[:8]],
            "hooks": [str(x) for x in result.get("hooks", [])[:8]],
            "formats": [str(x) for x in result.get("formats", [])[:8]],
            "avoid": [str(x) for x in result.get("avoid", [])[:8]],
            "summary": str(result.get("summary") or fallback["summary"]),
        }
    except Exception as exc:
        research = dict(fallback, error=str(exc)[:300])
    _save(db_factory, "research", research)
    return research


def audit_own_instagram(db_factory, graph_request, account):
    cached = _latest(db_factory, "audit", 6 * 3600)
    if cached:
        return cached
    try:
        result = graph_request(
            f"{account}/media?fields=id,caption,media_type,timestamp,like_count,comments_count&limit=25"
        )
        rows = []
        for item in result.get("data", []):
            likes = int(item.get("like_count") or 0)
            comments = int(item.get("comments_count") or 0)
            rows.append({
                "caption": str(item.get("caption") or "")[:500],
                "media_type": str(item.get("media_type") or ""),
                "likes": likes,
                "comments": comments,
                "score": likes + comments * 3,
            })
        rows.sort(key=lambda row: row["score"], reverse=True)
        audit = {
            "available": True,
            "sample_size": len(rows),
            "top_posts": rows[:5],
            "summary": "Priorize padrões dos posts com mais curtidas e, sobretudo, comentários; teste variações sem copiar.",
        }
    except Exception as exc:
        audit = {"available": False, "sample_size": 0, "top_posts": [], "error": str(exc)[:300]}
    _save(db_factory, "audit", audit)
    return audit


def optimized_caption(db_factory, graph_request, account, base_caption):
    research = research_trends(db_factory)
    audit = audit_own_instagram(db_factory, graph_request, account)
    try:
        result = _parse_json(_openai(
            """Você é o redator da Ragnar One. Melhore a legenda de um Reel em português brasileiro.
Use um gancho curto, linguagem natural, energia positiva, benefício sem alegações absolutas e CTA para comentar QUERO.
Priorize desejo, entretenimento, curiosidade e escolha; não dramatize sofrimento.
Use no máximo 5 hashtags relevantes. Não mencione pesquisa, algoritmo ou concorrentes.
PRESERVE EXATAMENTE qualquer linha de preços presente na legenda base; não altere valores, períodos nem moeda.
Não invente preço, desempenho, catálogo, teste grátis ou garantia. Retorne SOMENTE JSON válido:
{"caption":"...","reason":"..."}.""",
            "LEGENDA BASE:\n" + base_caption[:4000] +
            "\n\nTENDÊNCIAS:\n" + json.dumps(research, ensure_ascii=False)[:7000] +
            "\n\nDESEMPENHO PRÓPRIO:\n" + json.dumps(audit, ensure_ascii=False)[:7000],
        ))
        caption = str(result.get("caption") or "").strip()
        if not caption or "quero" not in caption.lower():
            raise ValueError("Legenda gerada sem CTA QUERO")
        learning = {"caption": caption, "reason": str(result.get("reason") or "")[:700]}
        _save(db_factory, "caption", learning)
        return caption
    except Exception as exc:
        _save(db_factory, "caption_error", {"error": str(exc)[:300]})
        return base_caption

