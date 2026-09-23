"""Pesquisa tendências, aprende com o Instagram da Ragnar e melhora cada publicação."""
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
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
    """Sempre usa DIRETAMENTE a API OpenAI configurada no Railway do Ragnar."""
    key = _env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY não configurada no Ragnar")
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
            "User-Agent": "RagnarAgent/4.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return _json_text(json.load(response))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail[:500]}")


def _openai_multimodal(instructions, content):
    key = _env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY não configurada no Ragnar")
    body = {
        "model": _env("OPENAI_MODEL", "gpt-5.6-luna"),
        "instructions": instructions,
        "input": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "RagnarAgent/4.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return _json_text(json.load(response))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenAI QA HTTP {exc.code}: {detail[:500]}")


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
            "(SELECT id FROM content_intelligence ORDER BY created DESC LIMIT 160)"
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
        "formats": [
            "momento aspiracional",
            "pergunta de escolha",
            "surpresa visual",
            "humor leve",
            "energia de jogo",
        ],
        "avoid": [
            "homem sofrendo",
            "pessoa triste ou desesperada",
            "antes triste/depois feliz",
            "promessa de zero travamento",
            "métricas inventadas",
            "copiar criativos",
        ],
        "summary": "Gancho imediato, desejo, curiosidade, entretenimento visual forte e CTA simples para comentar QUERO.",
    }


def research_trends(db_factory, force=False):
    if not force:
        cached = _latest(db_factory, "research", 4 * 3600)
        if cached:
            return cached
    fallback = fallback_research()
    try:
        memory = _latest(db_factory, "audit", 14 * 86400) or {}
        result = _parse_json(_openai(
            """Você executa pesquisa atual para a Ragnar One no Brasil.
Pesquise sinais dos últimos 30 dias em conteúdo público sobre entretenimento, streaming, filmes, séries,
futebol ao vivo, descoberta de conteúdo e comportamento de audiência. Extraia MECANISMOS de atenção,
retenção, curiosidade, emoção positiva, comentários e compartilhamento; não copie posts, slogans, marcas
ou identidade de terceiros. Problemas como travamento, delay e suporte podem existir apenas como contexto
de copy. NUNCA recomende homem sofrendo, pessoa triste/desesperada, raiva, briga ou comparação
antes triste/depois feliz. Não invente métricas, não prometa viralização e não recomende pirataria.
Retorne SOMENTE JSON válido com signals, hooks, formats, avoid e summary.""",
            "Crie aprendizados práticos para a próxima publicação da Ragnar One. "
            "Compare com o histórico próprio quando disponível:\n"
            + json.dumps(memory, ensure_ascii=False)[:7000],
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
        research = dict(fallback, error=str(exc)[:400])
    _save(db_factory, "research", research)
    return research


def _insight_metric(graph_request, media_id, metric):
    try:
        result = graph_request(
            f"{media_id}/insights?metric={urllib.parse.quote(metric)}"
        )
        data = result.get("data") or []
        if not data:
            return None
        item = data[0] or {}
        values = item.get("values") or []
        value = values[0].get("value") if values and isinstance(values[0], dict) else item.get("value")
        return float(value) if value is not None else None
    except Exception:
        return None


def audit_own_instagram(db_factory, graph_request, account, force=False):
    if not force:
        cached = _latest(db_factory, "audit", 4 * 3600)
        if cached:
            return cached
    try:
        result = graph_request(
            f"{account}/media?fields=id,caption,media_type,timestamp,like_count,comments_count&limit=20"
        )
        rows = []
        for item in (result.get("data") or [])[:12]:
            media_id = str(item.get("id") or "")
            likes = int(item.get("like_count") or 0)
            comments = int(item.get("comments_count") or 0)
            reach = _insight_metric(graph_request, media_id, "reach") if media_id else None
            saved = _insight_metric(graph_request, media_id, "saved") if media_id else None
            shares = _insight_metric(graph_request, media_id, "shares") if media_id else None
            weighted = likes + comments * 2 + (saved or 0) * 3 + (shares or 0) * 4
            score = weighted / reach if reach and reach > 0 else weighted
            rows.append({
                "caption": str(item.get("caption") or "")[:500],
                "media_type": str(item.get("media_type") or ""),
                "likes": likes,
                "comments": comments,
                "reach": reach,
                "saved": saved,
                "shares": shares,
                "score": score,
            })
        rows.sort(key=lambda row: row["score"], reverse=True)
        audit = {
            "available": bool(rows),
            "sample_size": len(rows),
            "top_posts": rows[:4],
            "weak_posts": rows[-3:] if rows else [],
            "summary": (
                "Use os melhores sinais da própria conta como referência, principalmente comentários, salvamentos "
                "e compartilhamentos quando disponíveis. Teste variações; não trate correlação como garantia."
                if rows else "Ainda não há amostra própria suficiente."
            ),
        }
    except Exception as exc:
        audit = {
            "available": False,
            "sample_size": 0,
            "top_posts": [],
            "weak_posts": [],
            "error": str(exc)[:400],
            "summary": "Auditoria própria indisponível nesta rodada.",
        }
    _save(db_factory, "audit", audit)
    return audit


def _fallback_plan(slot_index):
    variants = {
        0: {
            "kicker": "DÊ PLAY",
            "headline": "Seu momento começa agora.",
            "support": "Entretenimento para transformar uma noite comum em sessão especial.",
            "scene_direction": "momento de descoberta e entretenimento em ambiente premium com uso natural de dispositivos",
        },
        1: {
            "kicker": "FILMES & SÉRIES",
            "headline": "Seu sofá virou cinema.",
            "support": "Prepare a pipoca e escolha a próxima história.",
            "scene_direction": "noite de cinema em casa, aconchegante, desejável e cinematográfica",
        },
        2: {
            "kicker": "HOJE TEM JOGO",
            "headline": "Sua tela está pronta?",
            "support": "Futebol é expectativa, emoção e cada lance vivido junto.",
            "scene_direction": "energia de futebol, amigos ou família em clima de jogo e expectativa positiva",
        },
    }
    return variants.get(min(max(int(slot_index), 0), 2), variants[0])


def plan_post(db_factory, graph_request, account, slot_index, brand_context=""):
    """Equivalente ao planner/creator da Claire, mas usando a API OpenAI do Ragnar."""
    fallback = _fallback_plan(slot_index)
    research = research_trends(db_factory, force=True)
    audit = audit_own_instagram(db_factory, graph_request, account, force=True) if account else {
        "available": False, "summary": "Conta não disponível para auditoria."
    }
    try:
        result = _parse_json(_openai(
            """Você é o planner e diretor criativo da Ragnar One.
Escolha UMA ideia para a próxima publicação com base em pesquisa atual e desempenho da própria conta.
Objetivo: parar o scroll, gerar curiosidade, desejo de entretenimento, comentários e visitas.
Priorize emoção positiva, descoberta, futebol, cinema, maratona, família/amigos e uso natural de dispositivos.
Dor pode aparecer somente como contexto verbal. PROIBIDO: homem sofrendo, tristeza, desespero, raiva,
casal brigando, comparação triste/feliz, promessas absolutas, métricas inventadas, marcas/canais/clubes/personagens
de terceiros e inglês visível no criativo final.
A headline deve ter no máximo 7 palavras; apoio no máximo 12; kicker no máximo 4.
A direção de cena deve ser visual, cinematográfica, clara no celular e SEM texto dentro da imagem de fundo.
Retorne SOMENTE JSON:
{"kicker":"...","headline":"...","support":"...","scene_direction":"...","reason":"..."}.""",
            "HORÁRIO/ÍNDICE: " + str(slot_index)
            + "\nCONTEXTO DA MARCA:\n" + str(brand_context)[:3500]
            + "\nPESQUISA ATUAL:\n" + json.dumps(research, ensure_ascii=False)[:7000]
            + "\nDESEMPENHO PRÓPRIO:\n" + json.dumps(audit, ensure_ascii=False)[:7000],
        ))
        plan = {
            "kicker": str(result.get("kicker") or fallback["kicker"]).strip()[:60],
            "headline": str(result.get("headline") or fallback["headline"]).strip()[:120],
            "support": str(result.get("support") or fallback["support"]).strip()[:180],
            "scene_direction": str(result.get("scene_direction") or fallback["scene_direction"]).strip()[:900],
            "reason": str(result.get("reason") or "")[:600],
            "research_summary": str(research.get("summary") or "")[:900],
        }
        if not plan["headline"]:
            raise ValueError("planner sem headline")
    except Exception as exc:
        plan = dict(fallback, reason="fallback: " + str(exc)[:300], research_summary=str(research.get("summary") or "")[:900])
    _save(db_factory, "plan", plan)
    return plan


def optimized_caption(db_factory, graph_request, account, base_caption):
    research = research_trends(db_factory)
    audit = audit_own_instagram(db_factory, graph_request, account)
    try:
        result = _parse_json(_openai(
            """Você é o redator da Ragnar One. Melhore a legenda em português brasileiro.
Use um gancho curto, linguagem natural, energia positiva, benefício sem alegações absolutas e CTA para comentar QUERO.
Priorize desejo, entretenimento, curiosidade e escolha; não dramatize sofrimento.
Use no máximo 5 hashtags relevantes. Não mencione pesquisa, algoritmo ou concorrentes.
PRESERVE EXATAMENTE qualquer linha de preços presente na legenda base; não altere valores, períodos nem moeda.
Não invente preço, desempenho, catálogo, teste grátis ou garantia.
Retorne SOMENTE JSON válido: {"caption":"...","reason":"..."}.""",
            "LEGENDA BASE:\n" + base_caption[:4000]
            + "\n\nTENDÊNCIAS:\n" + json.dumps(research, ensure_ascii=False)[:7000]
            + "\n\nDESEMPENHO PRÓPRIO:\n" + json.dumps(audit, ensure_ascii=False)[:7000],
        ))
        caption = str(result.get("caption") or "").strip()
        if not caption or "quero" not in caption.lower():
            raise ValueError("Legenda gerada sem CTA QUERO")
        learning = {"caption": caption, "reason": str(result.get("reason") or "")[:700]}
        _save(db_factory, "caption", learning)
        return caption
    except Exception as exc:
        _save(db_factory, "caption_error", {"error": str(exc)[:400]})
        return base_caption


def audit_visual_quality(image_path, headline, support):
    """QA visual semelhante ao da Claire. Falha do QA não derruba a postagem."""
    try:
        raw = image_path.read_bytes()
        mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        text = _openai_multimodal(
            """Você é o controle de qualidade visual da Ragnar One.
Textos permitidos: RAGNAR ONE; a headline fornecida; a linha de apoio fornecida;
COMENTE QUERO E SAIBA MAIS; @ragnarplay1.
Reprove se houver outro texto inesperado, inglês visível, texto ilegível/aleatório, composição amadora,
headline pequena demais, excesso de informação, anatomia muito estranha ou aparência de card genérico.
Aprove apenas se parecer anúncio premium e legível no celular.
Retorne SOMENTE JSON: {"approved":true|false,"issues":["..."],"correction":"..."}.""",
            [
                {"type": "input_text", "text": f"Headline esperada: {headline}\nApoio esperado: {support}"},
                {"type": "input_image", "image_url": data_url},
            ],
        )
        result = _parse_json(text)
        return {
            "available": True,
            "approved": bool(result.get("approved")),
            "issues": [str(x) for x in result.get("issues", [])[:8]],
            "correction": str(result.get("correction") or "")[:900],
        }
    except Exception as exc:
        return {
            "available": False,
            "approved": True,
            "issues": ["qa_indisponivel"],
            "correction": "",
            "error": str(exc)[:400],
        }
