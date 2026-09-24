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
from datetime import datetime
from zoneinfo import ZoneInfo


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
        "summary": "Gancho imediato, desejo, curiosidade e conteúdo compartilhável que gere visitas ao perfil e novos seguidores.",
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

    def median(values):
        clean = sorted(float(x) for x in values if x is not None)
        if not clean:
            return 0.0
        middle = len(clean) // 2
        return clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2.0

    try:
        result = graph_request(
            f"{account}/media?fields=id,caption,media_type,timestamp,like_count,comments_count&limit=20"
        )
        rows = []
        for item in (result.get("data") or [])[:16]:
            media_id = str(item.get("id") or "")
            likes = int(item.get("like_count") or 0)
            comments = int(item.get("comments_count") or 0)
            reach = _insight_metric(graph_request, media_id, "reach") if media_id else None
            saved = _insight_metric(graph_request, media_id, "saved") if media_id else None
            shares = _insight_metric(graph_request, media_id, "shares") if media_id else None
            weighted = likes + comments * 2 + (saved or 0) * 3 + (shares or 0) * 4
            score = weighted / reach if reach and reach > 0 else weighted

            timestamp_text = str(item.get("timestamp") or "")
            reach_velocity = None
            timestamp_epoch = 0.0
            if timestamp_text:
                try:
                    from datetime import datetime
                    timestamp_epoch = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00")).timestamp()
                    age_hours = max(0.0, (time.time() - timestamp_epoch) / 3600.0)
                    if reach and reach > 0 and age_hours >= 3:
                        reach_velocity = float(reach) / max(3.0, min(age_hours, 72.0))
                except (TypeError, ValueError):
                    timestamp_epoch = 0.0

            rows.append({
                "caption": str(item.get("caption") or "")[:500],
                "media_type": str(item.get("media_type") or ""),
                "timestamp": timestamp_text,
                "timestamp_epoch": timestamp_epoch,
                "likes": likes,
                "comments": comments,
                "reach": reach,
                "saved": saved,
                "shares": shares,
                "score": score,
                "reach_velocity": reach_velocity,
            })

        median_reach = median([row["reach"] for row in rows if row.get("reach") and row["reach"] > 0])
        reliable_floor = max(20.0, median_reach * 0.75)
        reliable = [row for row in rows if float(row.get("reach") or 0) >= reliable_floor]
        ranking_pool = reliable or rows
        top_posts = sorted(ranking_pool, key=lambda row: row["score"], reverse=True)[:4]
        weak_posts = sorted(ranking_pool, key=lambda row: row["score"])[:3]

        format_values = {}
        for row in rows:
            velocity = row.get("reach_velocity")
            if velocity is None:
                continue
            key = str(row.get("media_type") or "POST").upper()
            format_values.setdefault(key, []).append(float(velocity))
        format_stats = sorted(
            [
                {"format": key, "median_reach_velocity": median(values), "posts": len(values)}
                for key, values in format_values.items()
            ],
            key=lambda item: item["median_reach_velocity"],
            reverse=True,
        )
        if (
            len(format_stats) >= 2
            and format_stats[0]["median_reach_velocity"] > format_stats[1]["median_reach_velocity"] * 1.3
        ):
            format_signal = (
                format_stats[0]["format"] + " está distribuindo mais rápido que "
                + format_stats[1]["format"]
                + " após ajuste pela idade dos posts. Reaproveite o mecanismo do formato vencedor "
                  "e evite repetição visual."
            )
        else:
            format_signal = "Não há diferença forte e confiável entre formatos nesta amostra."

        ordered = sorted(
            [row for row in rows if row.get("reach_velocity") is not None and row.get("timestamp_epoch")],
            key=lambda row: row["timestamp_epoch"],
            reverse=True,
        )
        half = min(6, len(ordered) // 2)
        recent_velocity = median([row["reach_velocity"] for row in ordered[:half]]) if half >= 3 else 0.0
        prior_velocity = median([row["reach_velocity"] for row in ordered[half:half * 2]]) if half >= 3 else 0.0
        trend_ratio = recent_velocity / prior_velocity if prior_velocity > 0 else None
        if trend_ratio is not None and trend_ratio < 0.7:
            trend_signal = (
                "A velocidade de alcance recente caiu mesmo após ajuste pela idade dos posts; "
                "variar gancho, tema e composição visual é prioridade."
            )
        else:
            trend_signal = "A velocidade de alcance recente não mostra queda forte após ajuste pela idade dos posts."

        # Aprende os melhores horários usando alcance ajustado pela idade + ações
        # de alto valor. Enquanto houver poucos horários testados, mantém um slot
        # de exploração para descobrir janelas melhores do que a agenda antiga.
        hour_values = {}
        hour_counts = {}
        for row in rows:
            timestamp_text = str(row.get("timestamp") or "")
            if not timestamp_text:
                continue
            try:
                published = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00")).astimezone(
                    ZoneInfo("America/Sao_Paulo")
                )
                hour = int(published.hour)
            except (TypeError, ValueError):
                continue
            if hour < 6 or hour > 23:
                continue
            velocity = row.get("reach_velocity")
            distribution = float(velocity) if velocity is not None else float(row.get("reach") or 0) / 72.0
            timing_score = (
                distribution
                + float(row.get("shares") or 0) * 1.6
                + float(row.get("saved") or 0) * 1.2
                + float(row.get("comments") or 0) * 0.35
                + float(row.get("likes") or 0) * 0.08
            )
            hour_values.setdefault(hour, []).append(timing_score)
            hour_counts[hour] = hour_counts.get(hour, 0) + 1

        hour_stats = sorted(
            [
                {"hour": hour, "score": median(values), "posts": hour_counts.get(hour, len(values))}
                for hour, values in hour_values.items()
            ],
            key=lambda item: (-item["score"], -item["posts"], item["hour"]),
        )

        selected_hours = []
        def can_use_hour(hour):
            return all(abs(int(hour) - int(current)) >= 4 for current in selected_hours)

        exploit_limit = 3 if len(hour_stats) >= 6 else 2
        for item in hour_stats:
            if len(selected_hours) >= exploit_limit:
                break
            hour = int(item["hour"])
            if can_use_hour(hour):
                selected_hours.append(hour)

        if len(selected_hours) < 3:
            tested = {int(item["hour"]) for item in hour_stats}
            day_key = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d")
            seed = sum(ord(char) for char in day_key)
            exploration = []
            for hour in range(7, 23):
                if not can_use_hour(hour):
                    continue
                if hour_stats:
                    modeled = max(
                        float(item["score"]) * (0.82 ** abs(int(item["hour"]) - hour))
                        for item in hour_stats
                    )
                    top_score = float(hour_stats[0]["score"] or 1)
                else:
                    modeled = 0.0
                    top_score = 1.0
                novelty = 0.0 if hour in tested else max(0.05, top_score * 0.08)
                tie_breaker = ((seed + hour * 17) % 13) / 10000.0
                exploration.append((modeled + novelty + tie_breaker, hour))
            for _, hour in sorted(exploration, reverse=True):
                if len(selected_hours) >= 3:
                    break
                if can_use_hour(hour):
                    selected_hours.append(hour)

        for hour in [9, 14, 20, 8, 13, 18, 22]:
            if len(selected_hours) >= 3:
                break
            if can_use_hour(hour):
                selected_hours.append(hour)

        selected_hours = sorted(selected_hours[:3])
        if len(selected_hours) != 3:
            selected_hours = [9, 14, 20]
        best_post_times = [f"{hour:02d}:00" for hour in selected_hours]

        audit = {
            "available": bool(rows),
            "sample_size": len(rows),
            "top_posts": top_posts,
            "weak_posts": weak_posts,
            "format_stats": format_stats,
            "format_signal": format_signal,
            "trend_signal": trend_signal,
            "trend_ratio": round(trend_ratio, 2) if trend_ratio is not None else None,
            "best_post_times": best_post_times,
            "hour_stats": hour_stats[:10],
            "summary": (
                format_signal + " " + trend_signal
                + " Ignore taxas chamativas em posts com alcance minúsculo; "
                  "use padrões com amostra suficiente e teste variações."
                if rows else "Ainda não há amostra própria suficiente."
            ),
        }
    except Exception as exc:
        audit = {
            "available": False,
            "sample_size": 0,
            "top_posts": [],
            "weak_posts": [],
            "format_stats": [],
            "best_post_times": ["09:00", "14:00", "20:00"],
            "hour_stats": [],
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
KPI principal: aumentar seguidores qualificados. META OPERACIONAL ASPIRACIONAL MOONSHOT: buscar atingir 1.000.000 de seguidores em até 28 dias, sem prometer resultado ao público. Objetivo: parar o scroll, gerar curiosidade, compartilhamentos, salvamentos, visitas ao perfil e motivo claro para seguir @ragnarplay1. Priorize séries e formatos recorrentes que façam a pessoa querer voltar. Priorize conteúdo original; evite reposts, marcas d'água e cópias de baixo valor. Teste ganchos diferentes e abandone rapidamente mecanismos abaixo da mediana recente da conta; replique o mecanismo dos vencedores sem copiar o criativo.
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
Use um gancho curto, linguagem natural, energia positiva e um motivo claro para seguir @ragnarplay1.
KPI principal: crescimento de seguidores, com meta operacional aspiracional MOONSHOT de atingir 1.000.000 de seguidores em 28 dias. Priorize compartilhamentos, salvamentos, visitas ao perfil, curiosidade recorrente e conteúdo em série.
Use no máximo 5 hashtags relevantes. Não mencione pesquisa, algoritmo ou concorrentes.
Não transforme toda legenda em anúncio; preço e venda direta são secundários nesta fase.
Não invente preço, desempenho, catálogo, teste grátis ou garantia.
Finalize com CTA natural para seguir o perfil e, quando fizer sentido, salvar ou compartilhar.
Retorne SOMENTE JSON válido: {"caption":"...","reason":"..."}.""",
            "LEGENDA BASE:\n" + base_caption[:4000]
            + "\n\nTENDÊNCIAS:\n" + json.dumps(research, ensure_ascii=False)[:7000]
            + "\n\nDESEMPENHO PRÓPRIO:\n" + json.dumps(audit, ensure_ascii=False)[:7000],
        ))
        caption = str(result.get("caption") or "").strip()
        if not caption or ("siga" not in caption.lower() and "@ragnarplay1" not in caption.lower()):
            raise ValueError("Legenda gerada sem CTA de crescimento")
        learning = {"caption": caption, "reason": str(result.get("reason") or "")[:700]}
        _save(db_factory, "caption", learning)
        return caption
    except Exception as exc:
        _save(db_factory, "caption_error", {"error": str(exc)[:400]})
        return base_caption


def audit_visual_quality(image_path, headline, support, kicker=""):
    """QA visual semelhante ao da Claire. Falha do QA não derruba a postagem."""
    try:
        raw = image_path.read_bytes()
        mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        text = _openai_multimodal(
            """Você é o controle de qualidade visual da Ragnar One.
Textos permitidos: RAGNAR ONE; o kicker fornecido; a headline fornecida; a linha de apoio fornecida;
COMENTE QUERO E SAIBA MAIS; @ragnarplay1.
Reprove se houver outro texto inesperado, inglês visível, texto ilegível/aleatório, composição amadora,
headline pequena demais, excesso de informação, anatomia muito estranha ou aparência de card genérico.
Aprove apenas se parecer anúncio premium e legível no celular.
Retorne SOMENTE JSON: {"approved":true|false,"issues":["..."],"correction":"..."}.""",
            [
                {"type": "input_text", "text": f"Kicker permitido: {kicker}\nHeadline esperada: {headline}\nApoio esperado: {support}"},
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
