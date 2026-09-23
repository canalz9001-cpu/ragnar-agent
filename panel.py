"""Painel web do Ragnar: configuracao, videos, aprovacao e publicacao."""
import base64
import cgi
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageOps
import content_intelligence

LOG = logging.getLogger("ragnar.panel")
BRAND = json.loads(Path(__file__).with_name("brand.json").read_text())

COOKIE_NAME = "ragnar_panel"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
DEFAULTS = {
    "greeting_text": "Olá! 👋 Sou o Ragnar, assistente da Ragnar One. Vi que você comentou QUERO. Escolha uma opção abaixo:",
    "website": "https://ragnarplay.online/",
    "whatsapp_number": "553491341688",
    "site_button_title": "Acessar site",
    "whatsapp_button_title": "Falar no WhatsApp",
    "banner_cta": 'Digite "QUERO" abaixo para saber mais',
    "assistant_rules": (
        "Atenda em português brasileiro, com mensagens curtas e naturais. "
        "Não invente informações. Direcione dúvidas comerciais para o site ou WhatsApp."
    ),
    "content_guidance": (
        "Criar conteúdo visual premium, cinematográfico e feito para parar o scroll para a Ragnar One. "
        "Paleta obrigatória: preto, verde e branco. Priorizar entretenimento aspiracional, futebol, noite de cinema, "
        "maratona, descoberta de conteúdo, pessoas se divertindo e uso natural de TV, smartphone, tablet ou notebook. "
        "Dor pode aparecer apenas como contexto de copy. É PROIBIDO usar homem sofrendo, pessoa triste/desesperada, "
        "rosto de raiva, casal brigando ou comparação antes triste/depois feliz. Variar cenário e mecanismo visual, "
        "evitar card genérico e buscar ação, curiosidade, emoção positiva e leitura imediata no celular."
    ),
    "posts_per_day": "3",
    "post_times": "09:00, 12:00, 18:00",
    "reel_caption": (
        "Chega de perder os melhores momentos por causa de travamentos.\\n\\n"
        'Digite "QUERO" abaixo para saber mais\\n\\n'
        "#RagnarOne #Streaming #Futebol"
    ),
}

_OPENAI_CACHE = {"at": 0.0, "data": None}
_NEXUS_CONFIG_CACHE = {"at": 0.0, "data": None}


def env(name):
    return os.environ.get(name, "").strip()


def data_root():
    root = Path(env("RAILWAY_VOLUME_MOUNT_PATH") or env("DATA_DIR") or "./data")
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_db():
    c = sqlite3.connect(data_root() / "ragnar.sqlite3", timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(
        "CREATE TABLE IF NOT EXISTS settings "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS media ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "original TEXT NOT NULL,"
        "cut TEXT NOT NULL UNIQUE,"
        "start REAL NOT NULL,"
        "duration REAL NOT NULL,"
        "created REAL NOT NULL,"
        "updated REAL NOT NULL,"
        "status TEXT NOT NULL,"
        "ig_container_id TEXT,"
        "ig_media_id TEXT,"
        "error TEXT)"
    )
    return c


def get_setting(key, default=None):
    fallback = DEFAULTS.get(key, default)
    try:
        with settings_db() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else fallback
    except sqlite3.Error:
        return fallback


def save_settings(values):
    allowed = set(DEFAULTS)
    now = time.time()
    rows = [(k, str(v).strip(), now) for k, v in values.items() if k in allowed]
    with settings_db() as c:
        c.executemany(
            "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            rows,
        )


def _secret():
    source = env("PANEL_SECRET") or env("META_APP_SECRET") or env("PANEL_PASSWORD")
    return hashlib.sha256(("ragnar-panel:" + source).encode()).digest()


def _session_cookie():
    expiry = str(int(time.time()) + 12 * 3600)
    payload = "ragnar|" + expiry
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _valid_session(environ):
    cookie = environ.get("HTTP_COOKIE", "")
    found = None
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith(COOKIE_NAME + "="):
            found = urllib.parse.unquote(part.split("=", 1)[1])
            break
    if not found:
        return False
    pieces = found.split("|")
    if len(pieces) != 3 or pieces[0] != "ragnar":
        return False
    try:
        if int(pieces[1]) < int(time.time()):
            return False
    except ValueError:
        return False
    payload = "|".join(pieces[:2])
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, pieces[2])


def _parse_urlencoded(environ):
    length = int(environ.get("CONTENT_LENGTH") or "0")
    raw = environ["wsgi.input"].read(length).decode("utf-8", "replace")
    parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in parsed.items()}


def _response(start_response, code, body, content_type="text/html; charset=utf-8", headers=None):
    raw = body.encode("utf-8") if isinstance(body, str) else body
    hdrs = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(raw))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
    ]
    if headers:
        hdrs.extend(headers)
    start_response(code, hdrs)
    return [raw]


def _redirect(start_response, location, cookie=None):
    headers = [("Location", location)]
    if cookie:
        headers.append(("Set-Cookie", cookie))
    start_response("303 See Other", headers)
    return [b""]


def _layout(title, content, notice=""):
    notice_html = f'<div class="notice">{html.escape(notice)}</div>' if notice else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Ragnar One</title>
<style>
:root{{--bg:#071018;--card:#101c27;--text:#f4f7fa;--muted:#9fb0bf;--red:#19c563;--line:#263746;--green:#20b26b;--amber:#ffbd52}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(135deg,#071018,#0a1621 55%,#10161b);color:var(--text);font-family:Inter,Arial,sans-serif}}
.wrap{{max-width:1180px;margin:auto;padding:24px}} .top{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}}
.brand{{font-weight:900;font-size:28px;letter-spacing:.6px}} .brand span{{color:var(--red)}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .cards3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.card{{background:rgba(16,28,39,.96);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 18px 50px #0005}}
.full{{grid-column:1/-1}} h1,h2,h3{{margin-top:0}} label{{display:block;font-weight:700;margin:13px 0 6px}}
input,textarea,select{{width:100%;border:1px solid #314657;background:#09131c;color:white;border-radius:11px;padding:12px;font:inherit}}
textarea{{min-height:96px;resize:vertical}} button,.btn{{display:inline-block;border:0;border-radius:11px;padding:11px 16px;background:var(--red);color:white;font-weight:800;text-decoration:none;cursor:pointer}}
.btn.secondary,button.secondary{{background:#203242}} button.good{{background:#168653}} button.warn{{background:#8a5b18}}
.notice{{background:#113824;border:1px solid #226d45;color:#caffdf;padding:12px 14px;border-radius:11px;margin-bottom:16px}}
.small{{font-size:13px;color:var(--muted)}} code{{background:#071018;padding:2px 6px;border-radius:6px}}
.login{{max-width:430px;margin:12vh auto}} .status{{display:inline-block;padding:5px 9px;border-radius:99px;background:#103a27;color:#baffd7;font-size:12px;font-weight:800}}
.status.warn{{background:#4b3513;color:#ffe3a8}} .status.bad{{background:#471821;color:#ffc1ca}} .metric{{font-size:24px;font-weight:900;margin:6px 0}}
.video-card{{border:1px solid var(--line);border-radius:15px;padding:16px;margin-top:14px;background:#0a151f}}
video{{width:100%;max-height:620px;background:#000;border-radius:12px;margin:10px 0 14px}} .actions{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
.actions form{{margin:0}} .two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:800px){{.grid,.cards3,.two{{grid-template-columns:1fr}} .full{{grid-column:auto}} .top{{align-items:flex-start;flex-direction:column}}}}
</style>
</head>
<body><div class="wrap">{notice_html}{content}</div></body></html>"""


def _login_page(message=""):
    msg = f'<p style="color:#ffb8bf">{html.escape(message)}</p>' if message else ""
    content = f"""
<div class="login card">
<div class="brand">RAGNAR <span>ONE</span></div>
<p class="muted">Painel de conteúdo e configuração do agente</p>
{msg}
<form method="post" action="/panel/login">
<label>Senha do painel</label>
<input type="password" name="password" autocomplete="current-password" required>
<p><button type="submit">Entrar</button></p>
</form>
</div>"""
    return _layout("Login", content)


def _safe_name(name):
    name = Path(name or "video").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return stem[:120] or "video.mp4"


def _register_cut(original, cut, start, duration):
    now = time.time()
    with settings_db() as c:
        c.execute(
            "INSERT OR REPLACE INTO media(original,cut,start,duration,created,updated,status,error) "
            "VALUES(?,?,?,?,?,?,?,NULL)",
            (original, cut, float(start), float(duration), now, now, "ready"),
        )


def _media_rows():
    with settings_db() as c:
        return c.execute(
            "SELECT original,cut,start,duration,created,updated,status,ig_container_id,ig_media_id,error "
            "FROM media ORDER BY created DESC LIMIT 50"
        ).fetchall()


def _latest_uploads():
    folder = data_root() / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:8]


def _status_badge(status):
    labels = {
        "ready": ("PRONTO PARA REVISÃO", ""),
        "approved": ("APROVADO", "warn"),
        "queued": ("NA FILA", "warn"),
        "processing": ("ENVIANDO AO INSTAGRAM", "warn"),
        "published": ("PUBLICADO", ""),
        "rejected": ("REPROVADO", "bad"),
        "publish_failed": ("FALHA NA PUBLICAÇÃO", "bad"),
    }
    label, klass = labels.get(status, (status.upper(), "warn"))
    return f'<span class="status {klass}">{html.escape(label)}</span>'


def _openai_status():
    now = time.time()
    if _OPENAI_CACHE["data"] is not None and now - _OPENAI_CACHE["at"] < 180:
        return _OPENAI_CACHE["data"]
    key = env("OPENAI_API_KEY")
    result = {
        "configured": bool(key),
        "valid": None,
        "label": "Não configurada" if not key else "Chave cadastrada",
        "expiry": "A chave da API não possui uma validade exibida automaticamente neste painel.",
        "credits": (
            "Adicione créditos na OpenAI para validar o uso do agente."
            if key else
            "Cadastre OPENAI_API_KEY no Railway."
        ),
        "month_cost": None,
    }

    # Verificação leve de autenticação. Ela NÃO é usada para concluir
    # que falta de saldo significa chave inválida.
    if key:
        req = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={"Authorization": "Bearer " + key, "User-Agent": "RagnarPanel/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if 200 <= response.status < 300:
                    result["valid"] = True
                    result["label"] = "Chave cadastrada e autenticada"
                    result["credits"] = "Autenticação OK. O saldo deve ser conferido no Billing da OpenAI."
        except urllib.error.HTTPError as exc:
            # Não rotular como 'chave inválida' aqui: falta de crédito,
            # restrição de projeto ou permissão também pode impedir uso.
            result["valid"] = None
            result["label"] = "Chave cadastrada — uso não validado"
            if exc.code == 429:
                result["credits"] = "Sem crédito/cota ou limite atingido. Confira o Billing da OpenAI."
            elif exc.code in (401, 403):
                result["credits"] = "A API não autorizou esta verificação. Confira saldo, projeto e permissões após adicionar créditos."
            else:
                result["credits"] = f"A verificação retornou HTTP {exc.code}. Confira o Billing e tente novamente."
        except Exception:
            result["valid"] = None
            result["label"] = "Chave cadastrada — verificação indisponível"
            result["credits"] = "Não foi possível verificar agora. O painel tentará novamente automaticamente."

    admin_key = env("OPENAI_ADMIN_KEY")
    if admin_key:
        start_time = int(time.time()) - 31 * 86400
        url = "https://api.openai.com/v1/organization/costs?" + urllib.parse.urlencode(
            {"start_time": start_time, "limit": 31}
        )
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + admin_key})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                payload = json.load(response)
            total = 0.0
            for bucket in payload.get("data", []):
                for item in bucket.get("results", []):
                    amount = item.get("amount", {})
                    if str(amount.get("currency", "")).lower() == "usd":
                        total += float(amount.get("value", 0) or 0)
            result["month_cost"] = total
        except Exception:
            pass

    _OPENAI_CACHE["at"] = now
    _OPENAI_CACHE["data"] = result
    return result

def _railway_status():
    total, used, free = shutil.disk_usage(data_root())
    token_present = bool(env("RAILWAY_API_TOKEN") or env("RAILWAY_WORKSPACE_TOKEN"))
    return {
        "project": env("RAILWAY_PROJECT_NAME") or "ragnar-agent",
        "environment": env("RAILWAY_ENVIRONMENT_NAME") or "production",
        "disk_used_mb": used / (1024 * 1024),
        "disk_free_mb": free / (1024 * 1024),
        "billing_connected": token_present,
        "credits": (
            "Token Railway conectado; o saldo exato depende dos campos de Billing disponíveis no workspace."
            if token_present else
            "Saldo automático ainda não conectado. É necessário um token de Workspace/Conta do Railway."
        ),
    }


def _dashboard(notice=""):
    s = {k: get_setting(k, v) for k, v in DEFAULTS.items()}
    openai = _openai_status()
    railway = _railway_status()
    rows = _media_rows()
    uploads = _latest_uploads()

    openai_badge = (
        '<span class="status">CHAVE AUTENTICADA</span>' if openai["valid"] is True
        else '<span class="status warn">CHAVE CADASTRADA</span>' if openai["configured"]
        else '<span class="status bad">CHAVE NÃO CADASTRADA</span>'
    )
    openai_cost = (
        f'US$ {openai["month_cost"]:.2f} nos últimos 31 dias'
        if openai["month_cost"] is not None else
        "Para custo automático, adicione uma OPENAI_ADMIN_KEY."
    )
    railway_badge = (
        '<span class="status">BILLING CONECTADO</span>' if railway["billing_connected"]
        else '<span class="status warn">BILLING NÃO CONECTADO</span>'
    )

    videos = ""
    for original, cut, start, duration, created, updated, status, container_id, media_id, error in rows:
        cut_q = urllib.parse.quote(cut)
        videos += f"""
<div class="video-card">
<div class="top" style="margin-bottom:4px">
<div><strong>{html.escape(cut)}</strong><div class="small">Corte {int(start)}s → {int(start+duration)}s · original: {html.escape(original)}</div></div>
{_status_badge(status)}
</div>
<video controls preload="metadata" src="/panel/media?file={cut_q}"></video>
"""
        if error:
            videos += f'<p style="color:#ffc1ca"><strong>Erro:</strong> {html.escape(error[:500])}</p>'
        if media_id:
            videos += f'<p class="small">Instagram media ID: <code>{html.escape(media_id)}</code></p>'
        videos += '<div class="actions">'
        if status in ("ready", "publish_failed"):
            videos += f"""
<form method="post" action="/panel/approve">
<input type="hidden" name="cut" value="{html.escape(cut)}">
<button class="good" type="submit">✓ Aprovar para agenda</button>
</form>
<form method="post" action="/panel/reject">
<input type="hidden" name="cut" value="{html.escape(cut)}">
<button class="secondary" type="submit">Reprovar</button>
</form>"""
        if status == "rejected":
            videos += f"""
<form method="post" action="/panel/approve">
<input type="hidden" name="cut" value="{html.escape(cut)}">
<button class="good" type="submit">Aprovar para agenda</button>
</form>"""
        videos += f"""
<form method="post" action="/panel/cut" class="actions">
<input type="hidden" name="filename" value="{html.escape(original)}">
<input style="width:110px" name="start" type="number" min="0" step="1" value="{int(start+duration)}" title="Início em segundos">
<input style="width:100px" name="duration" type="number" min="5" max="90" value="{int(duration)}" title="Duração">
<button class="warn" type="submit">Gerar outro corte</button>
</form>
</div></div>"""
    if not videos:
        videos = '<p class="muted">Nenhum corte pronto ainda. Assim que o processamento terminar, ele aparecerá aqui com os botões Aprovar e postar ou Não postar.</p>'

    uploads_html = ""
    for p in uploads:
        oq = urllib.parse.quote(p.name)
        mb = p.stat().st_size / (1024 * 1024)
        uploads_html += f"""
<div class="video-card">
<div class="top" style="margin-bottom:4px">
<div><strong>{html.escape(p.name)}</strong><div class="small">Vídeo original · {mb:.1f} MB</div></div>
<span class="status warn">ENVIADO</span>
</div>
<video controls preload="metadata" src="/panel/media?kind=original&file={oq}"></video>
<div class="actions">
<form method="post" action="/panel/cut" class="actions">
<input type="hidden" name="filename" value="{html.escape(p.name)}">
<label class="small">Início (s)</label>
<input style="width:95px" name="start" type="number" min="0" step="1" value="0">
<label class="small">Duração (s)</label>
<input style="width:95px" name="duration" type="number" min="5" max="90" value="30">
<button class="warn" type="submit">Gerar corte</button>
</form>
</div>
</div>"""
    if not uploads_html:
        uploads_html = '<p class="muted">Nenhum vídeo enviado ainda.</p>'

    content = f"""
<div class="top">
<div><div class="brand">RAGNAR <span>ONE</span></div><div class="muted">Painel do agente</div></div>
<div><span class="status">ONLINE</span> &nbsp; <a class="btn secondary" href="/panel/logout">Sair</a></div>
</div>

<div class="cards3">
<section class="card">
<h3>OpenAI</h3>{openai_badge}
<div class="metric">{html.escape(openai["label"])}</div>
<p class="small">{html.escape(openai["expiry"])}</p>
<p class="small"><strong>Créditos:</strong> {html.escape(openai["credits"])}</p>
<p class="small"><strong>Custos:</strong> {html.escape(openai_cost)}</p>
<a class="btn secondary" target="_blank" rel="noopener" href="https://platform.openai.com/settings/organization/billing/overview">Abrir Billing OpenAI</a>
</section>
<section class="card">
<h3>Railway</h3>{railway_badge}
<div class="metric">{html.escape(railway["project"])}</div>
<p class="small">Ambiente: {html.escape(railway["environment"])}</p>
<p class="small">Volume: {railway["disk_used_mb"]:.0f} MB usados · {railway["disk_free_mb"]:.0f} MB livres</p>
<p class="small"><strong>Créditos:</strong> {html.escape(railway["credits"])}</p>
<a class="btn secondary" target="_blank" rel="noopener" href="https://railway.com/dashboard">Abrir Railway</a>
</section>
<section class="card">
<h3>Publicação</h3><span class="status">APROVAÇÃO OBRIGATÓRIA</span>
<div class="metric">{html.escape(s['posts_per_day'])} posts/dia</div>
<p class="small">Horários: {html.escape(s['post_times'])}</p>
<p class="small">Nenhum vídeo é publicado antes de clicar em <strong>Aprovar e postar</strong>.</p>
</section>
</div>

<div class="grid" style="margin-top:18px">
<section class="card">
<h2>Enviar vídeo</h2>
<p class="muted">Ao terminar o upload, o Ragnar gera automaticamente um corte vertical 9:16 de 30 segundos com o CTA configurado.</p>
<form method="post" action="/panel/upload" enctype="multipart/form-data">
<label>Escolha o vídeo</label>
<input type="file" name="video" accept="video/mp4,video/quicktime,video/webm,.m4v" required>
<p><button type="submit">Enviar e gerar corte</button></p>
</form>
</section>

<section class="card">
<h2>Programação de conteúdo</h2>
<form method="post" action="/panel/settings">
<label>Postagens por dia</label>
<input name="posts_per_day" type="number" min="1" max="10" value="{html.escape(s['posts_per_day'])}">
<label>Horários</label>
<input name="post_times" value="{html.escape(s['post_times'])}" placeholder="09:00, 15:00, 21:00">
<label>Frase obrigatória nos banners e cortes</label>
<input name="banner_cta" value="{html.escape(s['banner_cta'])}">
<label>Legenda padrão dos cortes</label>
<textarea name="reel_caption">{html.escape(s['reel_caption'])}</textarea>
<p><button type="submit">Salvar programação</button></p>
</form>
</section>

<section class="card full">
<h2>Configurar Ragnar</h2>
<p class="muted">Altere frases, links e comportamento sem abrir GitHub ou Railway.</p>
<form method="post" action="/panel/settings">
<div class="two">
<div>
<label>Saudação quando alguém comenta QUERO</label>
<textarea name="greeting_text">{html.escape(s['greeting_text'])}</textarea>
<label>Site</label>
<input name="website" value="{html.escape(s['website'])}">
<label>Texto do botão do site</label>
<input name="site_button_title" maxlength="20" value="{html.escape(s['site_button_title'])}">
</div>
<div>
<label>Número do WhatsApp com DDI/DDD</label>
<input name="whatsapp_number" value="{html.escape(s['whatsapp_number'])}">
<label>Texto do botão do WhatsApp</label>
<input name="whatsapp_button_title" maxlength="20" value="{html.escape(s['whatsapp_button_title'])}">
<label>Frase obrigatória nos criativos</label>
<input name="banner_cta" value="{html.escape(s['banner_cta'])}">
</div>
</div>
<label>Regras de atendimento / personalidade do Ragnar</label>
<textarea name="assistant_rules">{html.escape(s['assistant_rules'])}</textarea>
<label>Orientações para criação de conteúdo</label>
<textarea name="content_guidance">{html.escape(s['content_guidance'])}</textarea>
<p><button type="submit">Salvar configuração do Ragnar</button></p>
</form>
</section>

<section class="card full">
<h2>Vídeos enviados</h2>
<p class="muted">O vídeo original fica aqui mesmo se um corte falhar. Você pode assistir e escolher o ponto inicial e a duração.</p>
{uploads_html}
</section>

<section class="card full">
<h2>Cortes para aprovação</h2>
<p class="muted">O corte pronto aparece aqui com player. Só será publicado quando você clicar em <strong>Aprovar e postar</strong>.</p>
{videos}
</section>
</div>"""
    return _layout("Painel", content, notice)


def _upload(environ):
    max_mb = int(env("MAX_UPLOAD_MB") or "300")
    content_length = int(environ.get("CONTENT_LENGTH") or "0")
    if content_length > max_mb * 1024 * 1024:
        raise ValueError(f"Arquivo maior que {max_mb} MB")
    form = cgi.FieldStorage(fp=environ["wsgi.input"], environ=environ, keep_blank_values=True)
    item = form["video"] if "video" in form else None
    if item is None or not getattr(item, "filename", ""):
        raise ValueError("Selecione um vídeo")
    name = _safe_name(item.filename)
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise ValueError("Formato de vídeo não permitido")
    folder = data_root() / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (str(int(time.time())) + "-" + name)
    with target.open("wb") as out:
        while True:
            chunk = item.file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return target.name


def _probe_video(path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,duration:format=duration",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        raise RuntimeError("Não foi possível ler o vídeo enviado.")
    try:
        return json.loads(result.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}


def _normalize_video(source):
    normalized_dir = data_root() / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    target = normalized_dir / (source.stem + "-normalizado.mp4")

    # Sempre recria para evitar reaproveitar normalização incompleta.
    target.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", "fps=30,format=yuv420p",
        "-af", "aresample=async=1:first_pts=0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts",
        str(target),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)

    if result.returncode != 0 or not target.exists() or target.stat().st_size < 2048:
        # Segunda tentativa sem áudio, útil para arquivos com trilha AAC problemática.
        target.unlink(missing_ok=True)
        fallback = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-map", "0:v:0",
            "-vf", "fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-an",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts",
            str(target),
        ]
        result = subprocess.run(fallback, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)

    if result.returncode != 0 or not target.exists() or target.stat().st_size < 2048:
        err = result.stderr.decode("utf-8", "replace")
        detail = (err[:1200] + "\n...\n" + err[-1000:]) if len(err) > 2300 else err
        raise RuntimeError("Não foi possível normalizar este vídeo. Detalhes técnicos: " + detail)

    return target


def _make_cta_overlay(path):
    text = get_setting("banner_cta", DEFAULTS["banner_cta"])
    img = Image.new("RGBA", (1080, 190), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (35, 15, 1045, 175),
        radius=34,
        fill=(0, 0, 0, 190),
        outline=(230, 45, 62, 255),
        width=5,
    )
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    size = 48
    while size >= 28:
        font = ImageFont.truetype(font_path, size=size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= 940:
            break
        size -= 2
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(
        ((1080 - tw) / 2, (190 - th) / 2 - 4),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )
    img.save(path)


def _generate_cut(filename, start, duration):
    source = data_root() / "uploads" / _safe_name(filename)
    if not source.exists() or source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Vídeo original não encontrado")

    start_n = max(0.0, min(float(start or 0), 36000.0))
    duration_n = max(5.0, min(float(duration or 30), 90.0))

    # Primeiro padroniza o arquivo. Isso resolve vídeos de celular/editor com
    # timestamps, edit lists, áudio ou container incomuns.
    normalized = _normalize_video(source)
    probe = _probe_video(normalized)
    try:
        fmt_duration = float(probe.get("format", {}).get("duration") or 0)
    except Exception:
        fmt_duration = 0.0
    if fmt_duration > 0 and start_n >= fmt_duration:
        raise ValueError(
            f"O ponto inicial ({int(start_n)}s) está depois do fim do vídeo ({int(fmt_duration)}s)."
        )
    if fmt_duration > 0:
        duration_n = min(duration_n, max(1.0, fmt_duration - start_n))

    cuts = data_root() / "cuts"
    cuts.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    output = cuts / (source.stem + f"-corte-{int(start_n)}s-{int(duration_n)}s-{stamp}.mp4")
    overlay = data_root() / f"cta-overlay-{stamp}.png"
    _make_cta_overlay(overlay)

    filter_complex = (
        "[0:v]fps=30,setpts=PTS-STARTPTS,"
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1[base];"
        "[1:v]format=rgba[cta];"
        "[base][cta]overlay=0:H-h-70:eof_action=repeat:shortest=0[outv]"
    )

    # -ss depois de -i: mais lento, porém muito mais confiável com vídeos de celular.
    cmd = [
        "ffmpeg", "-y",
        "-i", str(normalized),
        "-loop", "1", "-i", str(overlay),
        "-ss", str(start_n), "-t", str(duration_n),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(output),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=320)

    if result.returncode != 0 or not output.exists() or output.stat().st_size < 2048:
        # Última tentativa: sem CTA, mas ainda gera o corte para aprovação.
        output.unlink(missing_ok=True)
        fallback_cmd = [
            "ffmpeg", "-y",
            "-i", str(normalized),
            "-ss", str(start_n), "-t", str(duration_n),
            "-vf",
            "fps=30,scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,format=yuv420p",
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            str(output),
        ]
        result = subprocess.run(
            fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=320
        )

    try:
        overlay.unlink(missing_ok=True)
    except Exception:
        pass

    if result.returncode != 0 or not output.exists() or output.stat().st_size < 2048:
        err = result.stderr.decode("utf-8", "replace")
        detail = (err[:1200] + "\n...\n" + err[-1000:]) if len(err) > 2300 else err
        raise RuntimeError("Não foi possível gerar o corte deste vídeo. Detalhes técnicos: " + detail)

    _register_cut(source.name, output.name, start_n, duration_n)
    return output.name

def _video_path(filename, kind="cut"):
    safe = _safe_name(filename)
    folder = data_root() / ("cuts" if kind == "cut" else "uploads")
    path = folder / safe
    if not path.exists() or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    return path


def _serve_file(environ, start_response, path, public=False):
    size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    range_header = environ.get("HTTP_RANGE", "")
    start, end = 0, size - 1
    code = "200 OK"
    headers = [("Accept-Ranges", "bytes")]
    if range_header.startswith("bytes="):
        match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            end = max(start, end)
            code = "206 Partial Content"
            headers.append(("Content-Range", f"bytes {start}-{end}/{size}"))
    length = end - start + 1
    headers += [
        ("Content-Type", content_type),
        ("Content-Length", str(length)),
        ("Cache-Control", "private, no-store" if not public else "public, max-age=300"),
    ]
    start_response(code, headers)

    def chunks():
        remaining = length
        with path.open("rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return chunks()


def _public_signature(filename, expiry):
    payload = f"{filename}|{expiry}"
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def _public_url(filename):
    expiry = int(time.time()) + 6 * 3600
    sig = _public_signature(filename, expiry)
    domain = env("RAILWAY_PUBLIC_DOMAIN") or "ragnar-agent-production.up.railway.app"
    return (
        "https://" + domain + "/media-public?" +
        urllib.parse.urlencode({"file": filename, "exp": expiry, "sig": sig})
    )


def handle_public_media(environ, start_response):
    q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    filename = _safe_name(q.get("file", [""])[0])
    exp = q.get("exp", [""])[0]
    sig = q.get("sig", [""])[0]
    try:
        expiry = int(exp)
    except ValueError:
        return _response(start_response, "403 Forbidden", "Link inválido", "text/plain; charset=utf-8")
    if expiry < int(time.time()) or not hmac.compare_digest(sig, _public_signature(filename, expiry)):
        return _response(start_response, "403 Forbidden", "Link expirado ou inválido", "text/plain; charset=utf-8")
    path = _video_path(filename, "cut")
    if not path:
        return _response(start_response, "404 Not Found", "Vídeo não encontrado", "text/plain; charset=utf-8")
    return _serve_file(environ, start_response, path, public=True)



def _create_test_post_image():
    folder = data_root() / "test-posts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ragnar-test-post.png"
    img = Image.new("RGB", (1080, 1350), (7, 16, 24))
    draw = ImageDraw.Draw(img)
    font_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    title = ImageFont.truetype(font_bold, 92)
    sub = ImageFont.truetype(font_bold, 56)
    body = ImageFont.truetype(font_regular, 38)
    small = ImageFont.truetype(font_bold, 34)

    draw.rounded_rectangle((70, 90, 1010, 1260), radius=50, fill=(15, 28, 39), outline=(226, 45, 62), width=8)
    draw.text((110, 160), "RAGNAR", font=title, fill=(245, 247, 250))
    draw.text((560, 160), "ONE", font=title, fill=(226, 45, 62))
    draw.line((110, 290, 970, 290), fill=(226, 45, 62), width=5)
    draw.text((110, 390), "POSTAGEM DE TESTE", font=sub, fill=(245, 247, 250))
    draw.text((110, 520), "Automação do Instagram", font=body, fill=(170, 190, 205))
    draw.text((110, 585), "conectada ao agente Ragnar.", font=body, fill=(170, 190, 205))
    draw.rounded_rectangle((110, 760, 970, 930), radius=28, fill=(30, 52, 66))
    draw.text((175, 810), "TESTE DE PUBLICAÇÃO", font=small, fill=(245, 247, 250))
    draw.text((110, 1050), "Se você está vendo este post,", font=body, fill=(245, 247, 250))
    draw.text((110, 1110), "a conexão está funcionando.", font=body, fill=(245, 247, 250))
    img.save(path, "PNG")
    return path



def _wrap_lines(draw, text, font, max_width):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines



def _scheduled_theme(slot):
    cfg = _nexus_config()
    profile = _posting_profile()
    brief = _content_brief_for_slot(slot)
    niche = str(cfg.get("niche") or "Streaming")
    audience = str(profile.get("targetAudience") or "Misto")
    strategy = str(profile.get("contentStrategy") or "Vendas + engajamento")
    style = str(profile.get("visualStyle") or "Tecnológico premium")
    focus = str(profile.get("contentFocus") or brief or "Conteúdo comercial e de engajamento")
    tone = str(profile.get("tone") or "Firme, direto e profissional")
    avoid = str(profile.get("avoidTopics") or "Promessas irreais e poluição visual")

    index = 0
    for i, (hh, mm) in enumerate(_schedule_times()):
        if (hh, mm) == (slot.hour, slot.minute):
            index = i
            break

    brand_context = (
        f"Nicho: {niche}. Público: {audience}. Estratégia: {strategy}. "
        f"Estilo visual: {style}. Tom: {tone}. Foco: {focus}. "
        f"Evitar: {avoid}. Brief do horário: {brief}."
    )

    try:
        account = env("INSTAGRAM_ACCOUNT_ID")
        plan = content_intelligence.plan_post(
            settings_db,
            _graph_request,
            account,
            index,
            brand_context,
        )
    except Exception as exc:
        LOG.warning("planner_unavailable", exc_info=True)
        plan = {}

    fallbacks = {
        0: ("DÊ PLAY", "Seu momento começa agora.", "Entretenimento para transformar uma noite comum em sessão especial.", "momento premium de descoberta e entretenimento"),
        1: ("FILMES & SÉRIES", "Seu sofá virou cinema.", "Prepare a pipoca e escolha a próxima história.", "noite de cinema em casa, aconchegante e cinematográfica"),
        2: ("HOJE TEM JOGO", "Sua tela está pronta?", "Futebol é expectativa, emoção e cada lance vivido junto.", "energia de futebol e expectativa positiva antes da partida"),
    }
    fk, fh, fs, fscene = fallbacks.get(min(index, 2), fallbacks[0])

    kicker = str(plan.get("kicker") or fk)
    headline = str(plan.get("headline") or fh)
    support = str(plan.get("support") or fs)
    scene_direction = str(plan.get("scene_direction") or fscene)
    research_summary = str(plan.get("research_summary") or "")

    scene = (
        f"Create a premium vertical advertising photograph for the niche {niche}. "
        f"Target audience: {audience}. Creative brief: {brief or focus}. "
        f"Visual style: {style}. Communication tone: {tone}. "
        f"Current research guidance: {research_summary}. "
        f"Chosen scene direction: {scene_direction}. "
        f"Avoid: {avoid}. NO text, letters, logos, captions or watermarks inside the generated image. "
        "ATTENTION-FIRST COMPOSITION: create an upbeat, desirable entertainment moment with a clear focal point "
        "in the first glance. Prefer cinematic movie-night scenes, football excitement, friends/family enjoying "
        "the moment, content discovery, premium living-room atmosphere or natural multi-device use. "
        "Never depict suffering, sadness, anger, despair, frustration, crying, arguing, or a split-screen "
        "sad-versus-happy before/after. Do not generate generic text cards or empty lifeless scenes. "
        "Use premium cinematic lighting, believable devices, natural anatomy, energy, movement and visual curiosity. "
        "Leave the lower quarter darker for typography."
    )
    return {
        "kicker": kicker,
        "headline": headline,
        "support": support,
        "scene": scene,
        "slot_index": index,
        "brief": brief,
    }

def _post_ledger_id(slot):
    return f"ragnar-one:{slot:%Y%m%d-%H%M}"


def _nexus_post_event(slot, status, media_id="", error="", cost_delta_usd=None, model="", cost_source=""):
    token = env("NEXUS_AGENT_TOKEN")
    if not token:
        return False
    url = env("NEXUS_POST_EVENT_URL") or (
        "https://servidor-global-play-production.up.railway.app/"
        "api/agent/ragnar-one/posts/event"
    )
    body = {
        "postId": _post_ledger_id(slot),
        "scheduledFor": slot.isoformat(),
        "scheduledHour": slot.strftime("%H:%M"),
        "status": status,
    }
    if media_id:
        body["mediaId"] = str(media_id)
    if error:
        body["error"] = str(error)[:900]
    if model:
        body["model"] = str(model)
    if cost_source:
        body["costSource"] = str(cost_source)
    if cost_delta_usd is not None:
        try:
            value = float(cost_delta_usd)
            if value > 0:
                body["costDeltaUsd"] = value
        except (TypeError, ValueError):
            pass
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RagnarAgent-Nexus/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        LOG.warning("nexus_post_event_unavailable", exc_info=True)
        return False


def _image_usage_cost_usd(usage, model):
    if not isinstance(usage, dict):
        return None
    name = str(model or "")
    if name.startswith("gpt-image-2.5"):
        rates = {"text_in": 5.0, "image_in": 8.0, "cached_image_in": 2.0, "image_out": 30.0}
    elif name == "gpt-image-2":
        rates = {"text_in": 2.5, "image_in": 4.0, "cached_image_in": 1.0, "image_out": 15.0}
    else:
        return None
    details = usage.get("input_tokens_details") or usage.get("input_details") or {}
    try:
        text_in = float(details.get("text_tokens", usage.get("input_text_tokens", usage.get("input_tokens", 0))) or 0)
        image_in = float(details.get("image_tokens", usage.get("input_image_tokens", 0)) or 0)
        cached_image_in = float(details.get("cached_image_tokens", 0) or 0)
        image_out = float(usage.get("output_tokens", usage.get("output_image_tokens", 0)) or 0)
    except (TypeError, ValueError):
        return None
    value = (
        max(0, text_in) * rates["text_in"]
        + max(0, image_in - cached_image_in) * rates["image_in"]
        + max(0, cached_image_in) * rates["cached_image_in"]
        + max(0, image_out) * rates["image_out"]
    ) / 1000000.0
    return value if value >= 0 else None


def _nexus_generate_image(payload):
    """
    Compatibilidade legada. A automação do Ragnar NÃO usa proxy de imagem
    do servidor Global Play. Toda geração é feita diretamente na OpenAI
    usando OPENAI_API_KEY deste serviço.
    """
    return None

def _generate_local_fallback_scene(slot, primary, secondary, theme=None):
    """
    Fallback visual de verdade para quando a IA de imagem estiver indisponível.
    Em vez de um fundo vazio, cria uma cena editorial/ilustrada coerente com o horário:
    estabilidade/dispositivos, cinema ou futebol.
    """
    folder = data_root() / "test-posts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ragnar-scene-fallback-v2-{slot:%Y%m%d-%H%M}.png"
    if path.exists() and path.stat().st_size > 120000:
        return path

    size = (1024, 1536)
    p = _hex_rgb(primary, (34, 197, 94))
    s = _hex_rgb(secondary, (5, 8, 7))

    # Gradiente vertical escuro, rápido e limpo.
    strip = Image.new("RGB", (1, size[1]))
    strip_px = strip.load()
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        top = tuple(min(255, int(v * 1.65 + 10)) for v in s)
        bottom = tuple(max(0, int(v * 0.55)) for v in s)
        strip_px[0, y] = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
    base = strip.resize(size).convert("RGBA")

    # Luzes cinematográficas.
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((430, -160, 1280, 700), fill=(*p, 72))
    gd.ellipse((-420, 230, 420, 1100), fill=(10, 75, 45, 45))
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    base = Image.alpha_composite(base, glow)

    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)

    idx = int((theme or {}).get("slot_index", 0))
    if idx >= 2:
        # ===== Estádio noturno =====
        # Arquibancadas.
        d.ellipse((-220, 245, 1244, 1120), fill=(8, 17, 14, 255), outline=(*p, 90), width=5)
        d.ellipse((-145, 335, 1169, 1040), fill=(4, 10, 8, 255), outline=(230, 255, 240, 35), width=3)
        # Pontos de torcida.
        for row in range(9):
            y = 455 + row * 42
            for col in range(23):
                x = 78 + col * 40 + (row % 2) * 17
                alpha = 155 if (col + row) % 5 == 0 else 75
                color = (240, 250, 245, alpha) if (col + row) % 4 else (*p, alpha)
                d.ellipse((x, y, x + 5, y + 5), fill=color)
        # Campo em perspectiva.
        d.polygon([(118, 760), (906, 760), (1110, 1425), (-86, 1425)], fill=(7, 70, 34, 255))
        d.polygon([(170, 805), (854, 805), (980, 1320), (44, 1320)], outline=(205, 245, 220, 170), width=4)
        d.line((512, 805, 512, 1390), fill=(210, 245, 220, 120), width=4)
        d.ellipse((374, 930, 650, 1140), outline=(210, 245, 220, 115), width=4)
        # Refletores.
        for x in (92, 860):
            d.line((x, 300, x + (55 if x < 500 else -55), 755), fill=(90, 110, 100, 255), width=11)
            d.rounded_rectangle((x - 58, 260, x + 72, 330), radius=10, fill=(210, 235, 225, 235))
            for yy in range(274, 316, 18):
                for xx in range(x - 42, x + 55, 24):
                    d.ellipse((xx, yy, xx + 8, yy + 8), fill=(255, 255, 235, 255))
        # Bola em primeiro plano.
        bx, by, br = 785, 1000, 128
        d.ellipse((bx-br, by-br, bx+br, by+br), fill=(238, 245, 241, 255), outline=(255,255,255,210), width=6)
        pent = [(bx,by-46),(bx+45,by-13),(bx+28,by+42),(bx-28,by+42),(bx-45,by-13)]
        d.polygon(pent, fill=(15, 22, 19, 255))
        for dx, dy in [(-69,-31),(70,-27),(-57,64),(58,70)]:
            d.polygon([(bx+dx,by+dy-24),(bx+dx+22,by+dy-7),(bx+dx+14,by+dy+20),(bx+dx-15,by+dy+20),(bx+dx-23,by+dy-7)], fill=(24,31,28,240))
    elif idx == 1:
        # ===== Sala / cinema premium =====
        # Parede e painel de TV.
        d.rounded_rectangle((96, 190, 928, 955), radius=36, fill=(8, 14, 18, 245), outline=(255,255,255,25), width=3)
        d.rounded_rectangle((145, 248, 879, 805), radius=26, fill=(1, 5, 8, 255), outline=(*p, 110), width=4)
        # Cena abstrata dentro da TV: horizonte/cidade cinematográfica.
        d.rectangle((164, 266, 860, 785), fill=(6, 18, 23, 255))
        d.ellipse((570, 300, 815, 545), fill=(*p, 95))
        for x, h in [(190,170),(250,240),(320,135),(378,285),(456,205),(522,315),(610,190),(676,250),(744,160),(804,220)]:
            d.rectangle((x, 785-h, x+42, 785), fill=(10, 24, 29, 255))
            for wy in range(785-h+22, 770, 34):
                d.rectangle((x+10, wy, x+16, wy+8), fill=(*p, 135))
                d.rectangle((x+26, wy, x+32, wy+8), fill=(230,245,238,80))
        # Reflexo e play sem texto.
        d.ellipse((462, 466, 562, 566), fill=(0,0,0,150), outline=(255,255,255,85), width=3)
        d.polygon([(500,490),(500,542),(540,516)], fill=(245,250,248,225))
        # Móvel e luz ambiente.
        d.rounded_rectangle((180, 842, 844, 925), radius=20, fill=(10, 18, 16, 255))
        d.rectangle((218, 925, 250, 1050), fill=(8, 12, 11, 255))
        d.rectangle((774, 925, 806, 1050), fill=(8, 12, 11, 255))
        # Sofá em primeiro plano.
        d.rounded_rectangle((82, 1040, 942, 1430), radius=88, fill=(8, 11, 12, 255), outline=(255,255,255,20), width=3)
        d.rounded_rectangle((140, 990, 472, 1230), radius=70, fill=(14, 21, 20, 255))
        d.rounded_rectangle((552, 990, 884, 1230), radius=70, fill=(14, 21, 20, 255))
        # Controle remoto no apoio.
        d.rounded_rectangle((485, 1110, 548, 1260), radius=22, fill=(24, 31, 29, 255), outline=(*p, 120), width=3)
        d.ellipse((505, 1130, 528, 1153), fill=(*p, 210))
    else:
        # ===== Ecossistema de dispositivos / estabilidade =====
        # TV ao fundo.
        d.rounded_rectangle((90, 220, 790, 840), radius=38, fill=(4, 9, 12, 250), outline=(255,255,255,28), width=3)
        d.rounded_rectangle((130, 260, 750, 790), radius=24, fill=(6, 22, 18, 255), outline=(*p, 90), width=4)
        # Paisagem visual no display.
        d.polygon([(130,650),(290,470),(420,600),(560,390),(750,610),(750,790),(130,790)], fill=(13, 68, 42, 255))
        d.ellipse((530, 315, 675, 460), fill=(*p, 130))
        # Smartphone em destaque.
        d.rounded_rectangle((610, 510, 945, 1165), radius=54, fill=(8, 12, 14, 255), outline=(210,245,230,90), width=5)
        d.rounded_rectangle((635, 565, 920, 1108), radius=38, fill=(5, 27, 18, 255))
        d.rounded_rectangle((672, 635, 882, 760), radius=18, fill=(*p, 105))
        d.ellipse((750, 660, 805, 715), fill=(0,0,0,120))
        d.polygon([(773,674),(773,704),(797,689)], fill=(245,250,247,230))
        for yy, ww in [(815,165),(865,205),(915,145),(965,188)]:
            d.rounded_rectangle((672, yy, 672+ww, yy+20), radius=10, fill=(215,238,226,80))
        # Notebook parcial.
        d.rounded_rectangle((95, 900, 565, 1210), radius=26, fill=(14, 20, 22, 255), outline=(255,255,255,30), width=3)
        d.rectangle((128, 934, 532, 1170), fill=(5, 38, 24, 255))
        d.polygon([(55,1210),(605,1210),(675,1320),(-15,1320)], fill=(20,26,27,255))
        # Arcos de conexão.
        for radius, alpha in [(190,110),(245,75),(300,45)]:
            d.arc((420-radius, 335-radius, 420+radius, 335+radius), 205, 335, fill=(*p,alpha), width=7)

    # Glow e profundidade sobre as formas.
    canvas = canvas.filter(ImageFilter.GaussianBlur(0.35))
    base = Image.alpha_composite(base, canvas)

    # Vinheta sutil.
    vignette = Image.new("RGBA", size, (0,0,0,0))
    vd = ImageDraw.Draw(vignette)
    for i in range(0, 190, 10):
        alpha = int(2 + i * 0.33)
        vd.rectangle((i, i, size[0]-i, size[1]-i), outline=(0,0,0,alpha), width=12)
    base = Image.alpha_composite(base, vignette)

    img = base.convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(1.07)
    img = ImageEnhance.Color(img).enhance(1.12)
    img.save(path, "PNG", compress_level=3)
    return path


def _generate_premium_scene(slot, theme, correction=""):
    folder = data_root() / "test-posts"
    folder.mkdir(parents=True, exist_ok=True)

    correction_key = hashlib.sha256(str(correction or "").encode("utf-8")).hexdigest()[:8]
    raw_path = folder / f"ragnar-scene-premium-v6-{slot:%Y%m%d-%H%M}-{correction_key}.png"
    if raw_path.exists() and raw_path.stat().st_size > 100000:
        return raw_path

    cfg = _nexus_config()
    profile = _posting_profile()
    primary = str(cfg.get("primaryColor") or "#19c563")
    secondary = str(cfg.get("secondaryColor") or "#030a07")
    prompt = (
        "Create a premium vertical social media advertising photograph. "
        "NO TEXT, NO LETTERS, NO LOGOS, NO WATERMARKS, NO UI WORDS. "
        "The final design will add typography later. Keep important subjects away from the bottom 32 percent "
        "because that area will hold typography and a call-to-action. "
        f"Use the client's preferred visual style: {profile.get('visualStyle') or 'premium cinematic'}. "
        f"Use the client's main brand color {primary} and secondary color {secondary} as lighting/accent inspiration. "
        "Show rich visual storytelling; avoid a plain background or poster-like text card. "
        "NON-NEGOTIABLE QUALITY GATE: the image must communicate entertainment, curiosity or excitement instantly. "
        "Never show a suffering man, sad person, angry face, despair, arguments or a before/after sad-versus-happy comparison. "
        "Prefer a positive cinematic moment, strong focal action, believable entertainment context and natural device use. "
        "Brand colors are accents, not the whole scene. "
        + theme["scene"]
    )
    if correction:
        prompt += (
            " Previous visual quality review requested these corrections: "
            + str(correction)[:900]
            + ". Fix them while keeping the background text-free."
        )

    key = env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY do Ragnar não configurada no Railway.")

    # Somente campos aceitos pelo endpoint OpenAI. Nada de IDs internos do NEXUS.
    payload = {
        "model": env("OPENAI_IMAGE_MODEL") or "gpt-image-2",
        "prompt": prompt,
        "size": "1024x1536",
        "quality": env("OPENAI_IMAGE_QUALITY") or "medium",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "RagnarAgent/4.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=210) as response:
            result = json.load(response)

        local_cost = _image_usage_cost_usd(result.get("usage"), payload.get("model"))
        if local_cost is not None and local_cost > 0:
            _nexus_post_event(
                slot,
                "generating",
                cost_delta_usd=local_cost,
                model=payload.get("model") or "",
                cost_source="openai_usage",
            )

        encoded = result.get("b64_json")
        if not encoded:
            data = result.get("data") or []
            encoded = data[0].get("b64_json") if data and isinstance(data[0], dict) else None
        if not encoded:
            raise RuntimeError("A OpenAI não retornou os bytes da imagem.")

        raw = base64.b64decode(encoded)
        if len(raw) < 100000:
            raise RuntimeError("Imagem premium retornada é pequena ou inválida.")
        raw_path.write_bytes(raw)
        return raw_path
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload_error = json.loads(raw)
            err = payload_error.get("error", {})
            code = err.get("code") or err.get("type") or err.get("message") or "openai_image_failed"
        except Exception:
            code = raw[:300] or "openai_image_failed"
        raise RuntimeError(f"OpenAI Image HTTP {exc.code}: {code}")
    except Exception as exc:
        print("PREMIUM_CREATIVE_BLOCKED error=" + str(exc)[:400], flush=True)
        raise

def _draw_centered(draw, box, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    x = box[0] + (box[2] - box[0] - width) / 2
    draw.text((x, box[1]), text, font=font, fill=fill)


def _create_scheduled_post_image(slot):
    folder = data_root() / "test-posts"
    folder.mkdir(parents=True, exist_ok=True)
    cfg = _nexus_config()
    profile = _posting_profile()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "slot": slot.strftime("%Y%m%d-%H%M"),
                "primary": cfg.get("primaryColor"),
                "secondary": cfg.get("secondaryColor"),
                "profile": profile,
                "renderer": "premium-v6-claire-pipeline",
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:10]
    path = folder / f"ragnar-premium-v6-{slot:%Y%m%d-%H%M}-{fingerprint}.png"
    if path.exists() and path.is_file() and path.stat().st_size > 150000:
        return path

    theme = _scheduled_theme(slot)
    correction = ""
    last_issues = []

    for attempt in range(1, 4):
        source = _generate_premium_scene(slot, theme, correction=correction)

        try:
            src = Image.open(source).convert("RGB")
        except Exception as exc:
            raise RuntimeError("A cena premium gerada não pôde ser aberta.") from exc

        img = ImageOps.fit(src, (1080, 1350), method=Image.Resampling.LANCZOS, centering=(0.5, 0.46))
        img = ImageEnhance.Contrast(img).enhance(1.08)
        img = ImageEnhance.Color(img).enhance(1.04)

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(0, 250):
            alpha = int(95 * (1 - y / 250))
            od.rectangle((0, y, 1080, y + 1), fill=(0, 0, 0, alpha))
        for y in range(610, 1350):
            alpha = int(min(230, max(0, (y - 610) / 740 * 230)))
            od.rectangle((0, y, 1080, y + 1), fill=(2, 6, 5, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay)

        draw = ImageDraw.Draw(img)
        font_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        brand = ImageFont.truetype(font_bold, 54)
        kicker_font = ImageFont.truetype(font_bold, 24)
        headline = ImageFont.truetype(font_bold, 62)
        support = ImageFont.truetype(font_regular, 29)
        cta = ImageFont.truetype(font_bold, 38)
        footer = ImageFont.truetype(font_regular, 20)

        primary_rgb, secondary_rgb = _live_brand_colors()
        GREEN = (*primary_rgb, 255)
        WHITE = (248, 251, 249, 255)
        MUTED = (210, 222, 216, 255)

        draw.text((62, 48), "RAGNAR", font=brand, fill=WHITE, stroke_width=2, stroke_fill=(0,0,0,130))
        draw.text((300, 48), "ONE", font=brand, fill=GREEN, stroke_width=2, stroke_fill=(0,0,0,130))

        kicker_text = str(theme.get("kicker") or "RAGNAR ONE").upper()
        kicker_w = draw.textbbox((0,0), kicker_text, font=kicker_font)[2]
        pill = (62, 785, min(1010, 112 + kicker_w), 838)
        draw.rounded_rectangle(pill, radius=24, fill=(4, 14, 10, 188), outline=GREEN, width=2)
        draw.text((84, 800), kicker_text, font=kicker_font, fill=WHITE)

        y = 868
        lines = _wrap_lines(draw, theme["headline"], headline, 920)[:3]
        for line in lines:
            draw.text((62, y), line, font=headline, fill=WHITE, stroke_width=2, stroke_fill=(0,0,0,150))
            y += 72

        y += 10
        for line in _wrap_lines(draw, theme["support"], support, 900)[:2]:
            draw.text((64, y), line, font=support, fill=MUTED, stroke_width=1, stroke_fill=(0,0,0,110))
            y += 42

        profile = _posting_profile()
        cta_text = str(profile.get("cta") or 'Comente "QUERO" e saiba mais')
        short_cta = cta_text.replace('"', "").upper()
        if len(short_cta) > 34:
            short_cta = "COMENTE QUERO E SAIBA MAIS"
        cta_box = (62, 1182, 1018, 1280)
        draw.rounded_rectangle(cta_box, radius=32, fill=GREEN)
        _draw_centered(draw, (cta_box[0], cta_box[1] + 24, cta_box[2], cta_box[3]), short_cta, cta, WHITE)

        footer_text = str(cfg.get("instagram") or "@ragnarplay1")
        draw.text((64, 1306), footer_text, font=footer, fill=(220, 232, 226, 210))

        attempt_path = folder / f".qa-ragnar-{slot:%Y%m%d-%H%M}-{attempt}.png"
        img.convert("RGB").save(attempt_path, "PNG", optimize=False, compress_level=4)
        try:
            with Image.open(attempt_path) as check:
                check.verify()
            with Image.open(attempt_path) as check:
                if check.size != (1080, 1350) or check.format != "PNG":
                    raise RuntimeError("dimensao_ou_formato_invalido")
        except Exception as exc:
            attempt_path.unlink(missing_ok=True)
            raise RuntimeError("Criativo premium não passou na validação estrutural.") from exc

        qa = content_intelligence.audit_visual_quality(
            attempt_path,
            str(theme.get("headline") or ""),
            str(theme.get("support") or ""),
        )
        if qa.get("approved"):
            attempt_path.replace(path)
            if not qa.get("available"):
                print("VISUAL_QA_UNAVAILABLE publishing_after_structural_validation", flush=True)
            else:
                print(f"VISUAL_QA_APPROVED attempt={attempt}", flush=True)
            return path

        last_issues = qa.get("issues") or []
        correction = qa.get("correction") or "; ".join(str(x) for x in last_issues)
        print(
            f"VISUAL_QA_REJECTED attempt={attempt} issues={str(last_issues)[:500]}",
            flush=True,
        )
        attempt_path.unlink(missing_ok=True)

    raise RuntimeError(
        "Criativo reprovado pelo controle de qualidade: "
        + "; ".join(str(x) for x in last_issues)[:800]
    )

def _ragnar_price_line():
    plans = BRAND.get("plans_brl") or {}
    return (
        f"1 MÊS R$ {plans.get('mensal', 25)} • "
        f"3 MESES R$ {plans.get('trimestral', 60)} • "
        f"6 MESES R$ {plans.get('semestral', 110)} • "
        f"12 MESES R$ {plans.get('anual', 190)}"
    )


def _scheduled_caption(slot):
    theme = _scheduled_theme(slot)
    profile = _posting_profile()
    cta = str(profile.get("cta") or 'Comente "QUERO" e saiba mais')
    hashtags = str(profile.get("hashtags") or "#RagnarOne #Streaming #Entretenimento")
    base = (
        theme["headline"] + "\n\n"
        + theme["support"] + "\n\n"
        + _ragnar_price_line() + "\n\n"
        + cta + "\n\n"
        + hashtags
    )
    try:
        account = env("INSTAGRAM_ACCOUNT_ID")
        if account:
            return content_intelligence.optimized_caption(
                settings_db,
                _graph_request,
                account,
                base,
            )
    except Exception:
        LOG.warning("caption_intelligence_unavailable", exc_info=True)
    return base


def _publish_scheduled_image(slot):
    # Regra absoluta: publica somente criativo premium gerado por IA.
    # Fallback local foi proibido porque não garante pessoa + TV/dispositivo no padrão aprovado.
    # Regra: publica somente uma imagem 4:5 válida. O tamanho em bytes não é
    # usado como critério de qualidade porque PNG otimizado pode ficar menor.
    image = _create_scheduled_post_image(slot)
    if not image.exists():
        raise RuntimeError("Publicação bloqueada: criativo premium ausente ou inválido.")
    try:
        with Image.open(image) as check:
            if check.size != (1080, 1350) or check.format != "PNG":
                raise RuntimeError("imagem_invalida")
            check.verify()
    except Exception as exc:
        raise RuntimeError("Publicação bloqueada: criativo premium ausente ou inválido.") from exc
    image_url = _public_file_url("test-image", image.name)
    caption = _scheduled_caption(slot)
    return _publish_image_url_now(image_url, caption, max_wait=120)

def _public_file_signature(kind, filename, expiry):
    payload = f"{kind}|{filename}|{expiry}"
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def _public_file_url(kind, filename):
    expiry = int(time.time()) + 3600
    sig = _public_file_signature(kind, filename, expiry)
    domain = env("RAILWAY_PUBLIC_DOMAIN") or "ragnar-agent-production.up.railway.app"
    return (
        "https://" + domain + "/public-file?" +
        urllib.parse.urlencode({"kind": kind, "file": filename, "exp": expiry, "sig": sig})
    )


def handle_public_file(environ, start_response):
    q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    kind = q.get("kind", [""])[0]
    filename = _safe_name(q.get("file", [""])[0])
    exp = q.get("exp", [""])[0]
    sig = q.get("sig", [""])[0]
    try:
        expiry = int(exp)
    except ValueError:
        return _response(start_response, "403 Forbidden", "Link inválido", "text/plain; charset=utf-8")
    if expiry < int(time.time()) or not hmac.compare_digest(sig, _public_file_signature(kind, filename, expiry)):
        return _response(start_response, "403 Forbidden", "Link expirado ou inválido", "text/plain; charset=utf-8")

    if kind == "test-image":
        path = data_root() / "test-posts" / filename
        if not path.exists() or not path.is_file():
            return _response(start_response, "404 Not Found", "Imagem não encontrada", "text/plain; charset=utf-8")
        return _serve_file(environ, start_response, path, public=True)

    return _response(start_response, "404 Not Found", "Arquivo não encontrado", "text/plain; charset=utf-8")


def _publish_test_image():
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account or not env("INSTAGRAM_ACCESS_TOKEN") or not env("META_API_VERSION"):
        raise RuntimeError("Credenciais do Instagram não estão completas.")
    image = _create_test_post_image()
    image_url = _public_file_url("test-image", image.name)
    caption = (
        "Teste de automação do Ragnar One ✅\\n\\n"
        "Se você está vendo esta publicação, a conexão de postagem com o Instagram está funcionando.\\n\\n"
        "#RagnarOne"
    )
    created = _graph_request(
        f"{account}/media",
        "POST",
        {"image_url": image_url, "caption": caption},
    )
    creation_id = str(created.get("id", ""))
    if not creation_id:
        raise RuntimeError("A Meta não retornou o ID do contêiner da publicação.")
    published = _graph_request(
        f"{account}/media_publish",
        "POST",
        {"creation_id": creation_id},
    )
    media_id = str(published.get("id", ""))
    if not media_id:
        raise RuntimeError("A Meta não retornou o ID da publicação.")
    return media_id

def _graph_request(path, method="GET", data=None):
    host = "https://graph.instagram.com"
    version = env("META_API_VERSION")
    token = env("INSTAGRAM_ACCESS_TOKEN")
    url = f"{host}/{version}/{path.lstrip('/')}"
    payload = None
    headers = {"Authorization": "Bearer " + token, "User-Agent": "RagnarAgent/1.0"}
    if data is not None:
        payload = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Meta HTTP {exc.code}: {detail[:500]}")


def _start_publish(cut):
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account or not env("INSTAGRAM_ACCESS_TOKEN") or not env("META_API_VERSION"):
        raise RuntimeError("Credenciais do Instagram não estão completas.")
    row = None
    with settings_db() as c:
        row = c.execute(
            "SELECT original,cut,status FROM media WHERE cut=?", (cut,)
        ).fetchone()
    if not row:
        raise RuntimeError("Corte não encontrado.")
    base_caption = get_setting("reel_caption", DEFAULTS["reel_caption"])
    caption = content_intelligence.optimized_caption(
        settings_db, _graph_request, account, base_caption
    )
    body = {
        "media_type": "REELS",
        "video_url": _public_url(cut),
        "caption": caption,
        "share_to_feed": "true",
    }
    result = _graph_request(f"{account}/media", "POST", body)
    container_id = str(result.get("id", ""))
    if not container_id:
        raise RuntimeError("A Meta não retornou o ID do contêiner do Reel.")
    with settings_db() as c:
        c.execute(
            "UPDATE media SET status='processing',ig_container_id=?,error=NULL,updated=? WHERE cut=?",
            (container_id, time.time(), cut),
        )
    return container_id



def _publish_image_url_now(image_url, caption, max_wait=75):
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account or not env("INSTAGRAM_ACCESS_TOKEN") or not env("META_API_VERSION"):
        raise RuntimeError("Credenciais do Instagram não estão completas.")

    created = _graph_request(
        f"{account}/media",
        "POST",
        {"image_url": image_url, "caption": caption},
    )
    container_id = str(created.get("id", ""))
    if not container_id:
        raise RuntimeError("A Meta não retornou o ID do contêiner da publicação.")

    deadline = time.time() + max_wait
    last_status = ""
    while time.time() < deadline:
        time.sleep(3)
        status = _graph_request(
            f"{container_id}?fields=status_code,status",
            "GET",
        )
        status_code = str(status.get("status_code", "")).upper()
        last_status = str(status.get("status", "") or status_code)
        if status_code == "FINISHED":
            published = _graph_request(
                f"{account}/media_publish",
                "POST",
                {"creation_id": container_id},
            )
            media_id = str(published.get("id", ""))
            if not media_id:
                raise RuntimeError("A Meta não retornou o ID da publicação.")
            return media_id
        if status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError("A Meta não conseguiu processar a imagem: " + last_status)

    raise RuntimeError("A Meta ainda não deixou a imagem pronta para publicar: " + (last_status or "IN_PROGRESS"))


def handle_commercial_trigger(environ, start_response):
    q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    provided = q.get("token", [""])[0]
    expected = env("TEST_POST_TOKEN")
    if not expected or not hmac.compare_digest(provided, expected):
        return _response(start_response, "403 Forbidden", "Token inválido", "text/plain; charset=utf-8")

    image_url = env("MANUAL_POST_IMAGE_URL")
    caption = env("MANUAL_POST_CAPTION")
    if not image_url or not caption:
        return _response(start_response, "400 Bad Request", "Conteúdo comercial não configurado", "text/plain; charset=utf-8")

    fingerprint = hashlib.sha256((image_url + "\n" + caption).encode()).hexdigest()[:24]
    done_key = "manual_post_done_" + fingerprint
    with settings_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (done_key,)).fetchone()
    if row and row[0]:
        return _response(start_response, "200 OK", "COMMERCIAL_POST_ALREADY_PUBLISHED media_id=" + row[0], "text/plain; charset=utf-8")

    try:
        media_id = _publish_image_url_now(image_url, caption)
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (done_key, media_id, time.time()),
            )
        return _response(start_response, "200 OK", "COMMERCIAL_POST_SUCCESS media_id=" + media_id, "text/plain; charset=utf-8")
    except Exception as exc:
        return _response(start_response, "400 Bad Request", "COMMERCIAL_POST_ERROR " + str(exc), "text/plain; charset=utf-8")


def process_manual_image_post_once():
    if env("MANUAL_POST_ON_START").lower() != "true":
        return
    image_url = env("MANUAL_POST_IMAGE_URL")
    caption = env("MANUAL_POST_CAPTION")
    if not image_url or not caption:
        return

    fingerprint = hashlib.sha256((image_url + "\n" + caption).encode()).hexdigest()[:24]
    done_key = "manual_post_done_" + fingerprint
    container_key = "manual_post_container_" + fingerprint
    started_key = "manual_post_started_" + fingerprint
    error_key = "manual_post_error_" + fingerprint

    with settings_db() as c:
        done = c.execute("SELECT value FROM settings WHERE key=?", (done_key,)).fetchone()
        container = c.execute("SELECT value FROM settings WHERE key=?", (container_key,)).fetchone()
        started = c.execute("SELECT value FROM settings WHERE key=?", (started_key,)).fetchone()
    if done and done[0]:
        sync_key = f"schedule_image_nexus_sync_{slot:%Y%m%d_%H%M}"
        with settings_db() as c:
            synced = c.execute("SELECT value FROM settings WHERE key=?", (sync_key,)).fetchone()
        if not synced or not synced[0]:
            if _nexus_post_event(slot, "published", media_id=done[0]):
                with settings_db() as c:
                    c.execute(
                        "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                        (sync_key, "1", time.time()),
                    )
        return

    try:
        account = env("INSTAGRAM_ACCOUNT_ID")
        if not account or not env("INSTAGRAM_ACCESS_TOKEN") or not env("META_API_VERSION"):
            raise RuntimeError("Credenciais do Instagram não estão completas.")

        container_id = container[0] if container and container[0] else ""
        started_at = float(started[0]) if started and started[0] else 0.0

        if not container_id:
            created = _graph_request(
                f"{account}/media",
                "POST",
                {"image_url": image_url, "caption": caption},
            )
            container_id = str(created.get("id", ""))
            if not container_id:
                raise RuntimeError("A Meta não retornou o ID do contêiner da publicação.")
            now = time.time()
            with settings_db() as c:
                c.execute(
                    "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                    (container_key, container_id, now),
                )
                c.execute(
                    "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                    (started_key, str(now), now),
                )
            print("MANUAL_POST_CONTAINER_CREATED id=" + container_id, flush=True)
            return

        # Dá alguns segundos para a Meta buscar/processar a imagem antes da primeira consulta.
        if started_at and time.time() - started_at < 5:
            return

        status = _graph_request(
            f"{container_id}?fields=status_code,status",
            "GET",
        )
        status_code = str(status.get("status_code", "")).upper()
        status_text = str(status.get("status", ""))

        if status_code in ("IN_PROGRESS", ""):
            return

        if status_code == "FINISHED":
            published = _graph_request(
                f"{account}/media_publish",
                "POST",
                {"creation_id": container_id},
            )
            media_id = str(published.get("id", ""))
            if not media_id:
                raise RuntimeError("A Meta não retornou o ID da publicação.")
            with settings_db() as c:
                c.execute(
                    "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                    (done_key, media_id, time.time()),
                )
            print("MANUAL_POST_SUCCESS media_id=" + media_id, flush=True)
            return

        if status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError("A Meta não conseguiu processar a imagem: " + (status_text or status_code))

        # Outros estados: aguarda e consulta novamente.
        return

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (error_key, f"HTTP {exc.code}: {detail[:900]}", time.time()),
            )
        print(f"MANUAL_POST_ERROR HTTP {exc.code}: {detail[:900]}", flush=True)
    except Exception as exc:
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (error_key, str(exc)[:1000], time.time()),
            )
        print("MANUAL_POST_ERROR " + str(exc), flush=True)



def process_test_post_once():
    if env("TEST_POST_ON_START").lower() != "true":
        return
    with settings_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='test_post_done'").fetchone()
    if row and row[0]:
        return
    try:
        media_id = _publish_test_image()
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES('test_post_done',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (media_id, time.time()),
            )
        print("TEST_POST_SUCCESS media_id=" + media_id, flush=True)
    except Exception as exc:
        # Registra o erro para diagnóstico, mas não marca como concluído.
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES('test_post_last_error',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (str(exc)[:1000], time.time()),
            )
        print("TEST_POST_ERROR " + str(exc), flush=True)



def _nexus_config():
    """Read the client's live editorial rules from NEXUS AI.

    The cache prevents a network request on every worker tick. If NEXUS is
    temporarily unavailable, the last good configuration remains active.
    """
    now = time.time()
    if _NEXUS_CONFIG_CACHE["data"] is not None and now - _NEXUS_CONFIG_CACHE["at"] < 45:
        return _NEXUS_CONFIG_CACHE["data"]

    url = env("NEXUS_CONFIG_URL") or (
        "https://servidor-global-play-production.up.railway.app/"
        "api/agent-config/ragnar-one"
    )
    try:
        headers = {"Accept": "application/json", "User-Agent": "RagnarAgent-Nexus/1.0"}
        token = env("NEXUS_AGENT_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            url,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("invalid_nexus_payload")
        _NEXUS_CONFIG_CACHE["at"] = now
        _NEXUS_CONFIG_CACHE["data"] = payload
        return payload
    except Exception:
        LOG.warning("nexus_config_unavailable")
        _NEXUS_CONFIG_CACHE["at"] = now
        return _NEXUS_CONFIG_CACHE["data"] or {}


def _posting_profile():
    cfg = _nexus_config()
    profile = cfg.get("postingProfile") if isinstance(cfg, dict) else {}
    return profile if isinstance(profile, dict) else {}


def _hex_rgb(value, fallback):
    raw = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return tuple(int(raw[i:i+2], 16) for i in (1, 3, 5))
    return fallback


def _live_brand_colors():
    cfg = _nexus_config()
    primary = _hex_rgb(cfg.get("primaryColor"), (25, 197, 99))
    secondary = _hex_rgb(cfg.get("secondaryColor"), (3, 10, 7))
    return primary, secondary


def _content_brief_for_slot(slot):
    profile = _posting_profile()
    times = _schedule_times()
    index = 0
    for i, (hh, mm) in enumerate(times):
        if (hh, mm) == (slot.hour, slot.minute):
            index = i
            break
    fields = ["morningTheme", "afternoonTheme", "eveningTheme"]
    field = fields[min(index, len(fields) - 1)]
    return str(profile.get(field) or profile.get("contentFocus") or "").strip()


def _schedule_times():
    """
    Agenda autoritativa local do Ragnar.

    Igual ao modelo da Claire: os horários vêm da configuração do próprio
    serviço. O NEXUS pode orientar conteúdo, mas não pode apagar um horário
    de postagem por falha ou configuração remota.
    """
    raw = []
    explicit_times = env("AUTO_POST_TIMES")
    explicit_hours = env("AUTO_POST_HOURS")

    if explicit_times:
        raw = [x.strip() for x in explicit_times.split(",") if x.strip()]
    elif explicit_hours:
        for value in explicit_hours.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                hour = int(value)
            except ValueError:
                continue
            if 0 <= hour <= 23:
                raw.append(f"{hour:02d}:00")
    else:
        raw = [x.strip() for x in str(get_setting("post_times", DEFAULTS["post_times"])).split(",")]

    parsed = []
    seen = set()
    for value in raw:
        match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(value).strip())
        if not match:
            continue
        item = (int(match.group(1)), int(match.group(2)))
        if item not in seen:
            seen.add(item)
            parsed.append(item)

    return sorted(parsed)[:6] or [(9, 0), (12, 0), (18, 0)]

def process_scheduled_posts_once():
    times = _schedule_times()
    if not times:
        return

    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    due = []
    for hh, mm in times:
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot <= now:
            due.append(slot)

    if not due:
        return

    # Publica somente o horário mais recente devido. Isso recupera um horário
    # perdido após reinício sem despejar várias publicações antigas de uma vez.
    slot = max(due)
    slot_key = f"schedule_image_done_{slot:%Y%m%d_%H%M}"
    attempt_key = f"schedule_image_attempt_{slot:%Y%m%d_%H%M}"
    error_key = f"schedule_image_error_{slot:%Y%m%d_%H%M}"

    with settings_db() as c:
        done = c.execute("SELECT value FROM settings WHERE key=?", (slot_key,)).fetchone()
        attempt = c.execute("SELECT value FROM settings WHERE key=?", (attempt_key,)).fetchone()
        last_error = c.execute("SELECT value FROM settings WHERE key=?", (error_key,)).fetchone()

    attempt_age = None
    if attempt and attempt[0]:
        try:
            attempt_age = max(0, time.time() - float(attempt[0]))
        except (TypeError, ValueError):
            attempt_age = None
    if done and done[0]:
        return

    # Evita martelar a Meta em caso de erro temporário.
    # O intervalo pode ser reduzido temporariamente via Railway para diagnóstico.
    try:
        retry_seconds = max(30, int(env("SCHEDULE_RETRY_SECONDS") or "600"))
    except ValueError:
        retry_seconds = 600
    if attempt_age is not None and attempt_age < retry_seconds:
        return

    now_ts = time.time()
    with settings_db() as c:
        c.execute(
            "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
            (attempt_key, str(now_ts), now_ts),
        )

    print(f"SCHEDULE_IMAGE_ATTEMPT slot={slot:%Y-%m-%d_%H:%M}", flush=True)
    _nexus_post_event(slot, "generating")
    try:
        media_id = _publish_scheduled_image(slot)
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (slot_key, media_id, time.time()),
            )
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (error_key, "", time.time()),
            )
        _nexus_post_event(slot, "published", media_id=media_id)
        print(
            f"SCHEDULE_IMAGE_SUCCESS slot={slot:%Y-%m-%d_%H:%M} media_id={media_id}",
            flush=True,
        )
    except Exception as exc:
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (error_key, str(exc)[:1000], time.time()),
            )
        _nexus_post_event(slot, "failed", error=str(exc))
        print(
            f"SCHEDULE_IMAGE_ERROR slot={slot:%Y-%m-%d_%H:%M} error={str(exc)[:700]}",
            flush=True,
        )

def process_publication_once():
    try:
        with settings_db() as c:
            row = c.execute(
                "SELECT cut,ig_container_id,updated FROM media "
                "WHERE status='processing' AND ig_container_id IS NOT NULL "
                "ORDER BY updated LIMIT 1"
            ).fetchone()
        if not row:
            return
        cut, container_id, updated = row
        if time.time() - float(updated or 0) < 8:
            return
        result = _graph_request(
            f"{container_id}?fields=status_code,status",
            "GET",
        )
        status_code = str(result.get("status_code", "")).upper()
        status_text = str(result.get("status", ""))
        if status_code == "FINISHED":
            account = env("INSTAGRAM_ACCOUNT_ID")
            published = _graph_request(
                f"{account}/media_publish", "POST", {"creation_id": container_id}
            )
            media_id = str(published.get("id", ""))
            if not media_id:
                raise RuntimeError("A Meta não retornou o ID da publicação.")
            with settings_db() as c:
                c.execute(
                    "UPDATE media SET status='published',ig_media_id=?,error=NULL,updated=? WHERE cut=?",
                    (media_id, time.time(), cut),
                )
        elif status_code in ("ERROR", "EXPIRED"):
            with settings_db() as c:
                c.execute(
                    "UPDATE media SET status='publish_failed',error=?,updated=? WHERE cut=?",
                    (status_text or status_code, time.time(), cut),
                )
        else:
            with settings_db() as c:
                c.execute("UPDATE media SET updated=? WHERE cut=?", (time.time(), cut))
    except Exception as exc:
        try:
            with settings_db() as c:
                if row:
                    c.execute(
                        "UPDATE media SET status='publish_failed',error=?,updated=? WHERE cut=?",
                        (str(exc)[:700], time.time(), row[0]),
                    )
        except Exception:
            pass



def handle_test_trigger(environ, start_response):
    q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    provided = q.get("token", [""])[0]
    expected = env("TEST_POST_TOKEN")
    if not expected or not hmac.compare_digest(provided, expected):
        return _response(start_response, "403 Forbidden", "Token inválido", "text/plain; charset=utf-8")

    with settings_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='test_post_done'").fetchone()
    if row and row[0]:
        return _response(start_response, "200 OK", "Postagem de teste já executada.", "text/plain; charset=utf-8")

    try:
        media_id = _publish_test_image()
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES('test_post_done',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (media_id, time.time()),
            )
        return _response(
            start_response, "200 OK",
            "TEST_POST_SUCCESS media_id=" + media_id,
            "text/plain; charset=utf-8",
        )
    except Exception as exc:
        return _response(
            start_response, "400 Bad Request",
            "TEST_POST_ERROR " + str(exc),
            "text/plain; charset=utf-8",
        )


def _friendly_agent_error(message):
    text = str(message or "")
    low = text.lower()
    if "invalid_api_key" in low or ("openai" in low and "401" in low):
        return "OpenAI: chave inválida. Reconecte em Conexões."
    if "openai_not_connected" in low or "openai não conectada" in low:
        return "OpenAI: conexão pendente no NEXUS."
    if "429" in low or "rate_limit" in low or "insufficient_quota" in low:
        return "OpenAI: limite ou saldo indisponível."
    if "instagram" in low or "meta" in low:
        return "Instagram/Meta: falha na publicação. Verifique a conexão."
    return text[:180] if text else ""


def _scheduled_status_summary():
    latest_done = None
    latest_error = None
    try:
        with settings_db() as db:
            rows = db.execute(
                "SELECT key,value,updated FROM settings "
                "WHERE key LIKE 'schedule_image_done_%' OR key LIKE 'schedule_image_error_%' "
                "ORDER BY updated DESC LIMIT 80"
            ).fetchall()
        for key, value, updated in rows:
            if key.startswith("schedule_image_done_") and value and latest_done is None:
                suffix = key.replace("schedule_image_done_", "", 1)
                display = suffix
                try:
                    dt = datetime.strptime(suffix, "%Y%m%d_%H%M").replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
                    display = dt.strftime("%d/%m %H:%M")
                except ValueError:
                    pass
                latest_done = {
                    "slot": suffix,
                    "time": display,
                    "media_id": str(value),
                    "updated_at": float(updated or 0),
                }
            elif key.startswith("schedule_image_error_") and value and latest_error is None:
                latest_error = {
                    "message": _friendly_agent_error(value),
                    "updated_at": float(updated or 0),
                }
            if latest_done is not None and latest_error is not None:
                break
    except sqlite3.Error:
        return {"last_post": None, "last_error": None}

    last_error = None
    if latest_error and (not latest_done or latest_error["updated_at"] > latest_done["updated_at"]):
        last_error = latest_error["message"]
    return {"last_post": latest_done, "last_error": last_error}


def nexus_status_snapshot():
    openai = _openai_status()
    railway = _railway_status()
    cfg = _nexus_config()
    scheduled = _scheduled_status_summary()
    return {
        "agent": "Ragnar",
        "online": True,
        "nexus_connected": bool(cfg),
        "post_times": [f"{h:02d}:{m:02d}" for h, m in _schedule_times()],
        "last_post": scheduled.get("last_post"),
        "last_error": scheduled.get("last_error"),
        "openai": {
            "configured": bool(openai.get("configured")),
            "authenticated": openai.get("valid") is True,
            "month_cost_usd": openai.get("month_cost"),
            "status": openai.get("label"),
        },
        "railway": {
            "project": railway.get("project"),
            "environment": railway.get("environment"),
            "disk_used_mb": round(float(railway.get("disk_used_mb") or 0), 1),
            "disk_free_mb": round(float(railway.get("disk_free_mb") or 0), 1),
            "billing_connected": bool(railway.get("billing_connected")),
        },
    }


def handle(environ, start_response):
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "GET").upper()

    if path == "/panel/login":
        if method == "GET":
            return _response(start_response, "200 OK", _login_page())
        if method == "POST":
            password = _parse_urlencoded(environ).get("password", "")
            configured = env("PANEL_PASSWORD")
            if not configured:
                return _response(start_response, "503 Service Unavailable", _login_page("A senha do painel ainda não foi configurada."))
            if hmac.compare_digest(password, configured):
                cookie = (
                    COOKIE_NAME + "=" + urllib.parse.quote(_session_cookie()) +
                    "; Path=/panel; HttpOnly; SameSite=Lax; Max-Age=43200"
                )
                return _redirect(start_response, "/panel", cookie)
            return _response(start_response, "403 Forbidden", _login_page("Senha incorreta."))
        return _response(start_response, "405 Method Not Allowed", "Método não permitido", "text/plain; charset=utf-8")

    if path == "/panel/logout":
        return _redirect(
            start_response, "/panel/login",
            COOKIE_NAME + "=; Path=/panel; HttpOnly; SameSite=Lax; Max-Age=0"
        )

    if not _valid_session(environ):
        return _redirect(start_response, "/panel/login")

    if path in ("/panel", "/panel/"):
        return _response(start_response, "200 OK", _dashboard())

    if path == "/panel/test-post" and method == "POST":
        try:
            media_id = _publish_test_image()
            return _response(start_response, "200 OK", _dashboard("Postagem de teste publicada no Instagram. Media ID: " + media_id))
        except Exception as exc:
            return _response(start_response, "400 Bad Request", _dashboard("Falha ao publicar teste: " + str(exc)))

    if path == "/panel/media" and method == "GET":
        q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        kind = "original" if q.get("kind", [""])[0] == "original" else "cut"
        media = _video_path(q.get("file", [""])[0], kind)
        if not media:
            return _response(start_response, "404 Not Found", "Vídeo não encontrado", "text/plain; charset=utf-8")
        return _serve_file(environ, start_response, media)

    if path == "/panel/settings" and method == "POST":
        values = _parse_urlencoded(environ)
        save_settings(values)
        return _response(start_response, "200 OK", _dashboard("Configurações salvas. O Ragnar já usará as novas frases."))

    if path == "/panel/upload" and method == "POST":
        try:
            original = _upload(environ)
            cut = _generate_cut(original, 0, 30)
            return _response(
                start_response, "200 OK",
                _dashboard("Upload concluído. O corte de 30s já foi gerado automaticamente e está pronto para você assistir.")
            )
        except Exception as exc:
            return _response(start_response, "400 Bad Request", _dashboard(str(exc)))

    if path == "/panel/cut" and method == "POST":
        try:
            values = _parse_urlencoded(environ)
            name = _generate_cut(
                values.get("filename", ""),
                values.get("start", "0"),
                values.get("duration", "30"),
            )
            return _response(start_response, "200 OK", _dashboard("Novo corte gerado: " + name))
        except Exception as exc:
            return _response(start_response, "400 Bad Request", _dashboard(str(exc)))

    if path == "/panel/reject" and method == "POST":
        cut = _safe_name(_parse_urlencoded(environ).get("cut", ""))
        with settings_db() as c:
            c.execute("UPDATE media SET status='rejected',updated=? WHERE cut=?", (time.time(), cut))
        return _response(start_response, "200 OK", _dashboard("Corte reprovado. Gere outro trecho se quiser."))

    if path == "/panel/approve" and method == "POST":
        cut = _safe_name(_parse_urlencoded(environ).get("cut", ""))
        with settings_db() as c:
            c.execute(
                "UPDATE media SET status='queued',error=NULL,updated=? WHERE cut=?",
                (time.time(), cut),
            )
        return _response(
            start_response, "200 OK",
            _dashboard("Corte aprovado e colocado na agenda. O Ragnar publicará no próximo horário configurado.")
        )

    return _response(start_response, "404 Not Found", "Página não encontrada", "text/plain; charset=utf-8")