"""Painel web do Ragnar: configuracao, videos, aprovacao e publicacao."""
import cgi
import hashlib
import hmac
import html
import json
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
from PIL import Image, ImageDraw, ImageFont
import content_intelligence

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
        "Criar conteúdo visual premium, pouco texto e foco nas dores do cliente: "
        "travamentos em jogos, delay, filmes e séries travando e suporte que não responde."
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
:root{{--bg:#071018;--card:#101c27;--text:#f4f7fa;--muted:#9fb0bf;--red:#e22d3e;--line:#263746;--green:#20b26b;--amber:#ffbd52}}
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
    themes = {
        9: {
            "kicker": "COMECE O DIA SEM TRAVAR",
            "headline": "Seu streaming trava justo na hora do jogo?",
            "support": "Estabilidade e suporte fazem diferença quando cada lance importa.",
        },
        12: {
            "kicker": "ENTRETENIMENTO SEM ESTRESSE",
            "headline": "Filmes e séries travando acabam com a experiência.",
            "support": "Tenha uma experiência mais estável para assistir quando quiser.",
        },
        18: {
            "kicker": "HOJE TEM JOGO?",
            "headline": "Não deixe o travamento decidir o melhor momento da partida.",
            "support": "Ragnar One: estabilidade, conteúdo e suporte para você aproveitar.",
        },
    }
    return themes.get(slot.hour, themes[18])


def _create_scheduled_post_image(slot):
    folder = data_root() / "test-posts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ragnar-auto-{slot:%Y%m%d-%H%M}.png"
    if path.exists() and path.is_file():
        return path

    theme = _scheduled_theme(slot)
    img = Image.new("RGB", (1080, 1350), (6, 13, 20))
    draw = ImageDraw.Draw(img)
    font_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    brand = ImageFont.truetype(font_bold, 82)
    kicker = ImageFont.truetype(font_bold, 31)
    headline = ImageFont.truetype(font_bold, 64)
    support = ImageFont.truetype(font_regular, 36)
    cta = ImageFont.truetype(font_bold, 54)
    footer = ImageFont.truetype(font_regular, 28)

    # Fundo com profundidade, mantendo visual premium e limpo.
    for y in range(1350):
        shade = int(6 + (y / 1350) * 11)
        draw.line((0, y, 1080, y), fill=(shade, shade + 7, shade + 13))
    draw.ellipse((650, -170, 1250, 430), fill=(32, 16, 23))
    draw.ellipse((-260, 850, 380, 1490), fill=(11, 27, 38))

    draw.rounded_rectangle((62, 62, 1018, 1288), radius=48, fill=(9, 19, 28), outline=(226, 45, 62), width=6)
    draw.text((105, 125), "RAGNAR", font=brand, fill=(245, 247, 250))
    draw.text((600, 125), "ONE", font=brand, fill=(226, 45, 62))
    draw.rounded_rectangle((105, 270, 635, 332), radius=25, fill=(226, 45, 62))
    draw.text((132, 282), theme["kicker"], font=kicker, fill=(255, 255, 255))

    y = 415
    for line in _wrap_lines(draw, theme["headline"], headline, 820):
        draw.text((105, y), line, font=headline, fill=(245, 247, 250))
        y += 82

    y += 42
    for line in _wrap_lines(draw, theme["support"], support, 820):
        draw.text((105, y), line, font=support, fill=(174, 193, 208))
        y += 55

    draw.rounded_rectangle((105, 970, 975, 1135), radius=30, fill=(226, 45, 62))
    draw.text((190, 1017), "COMENTE QUERO", font=cta, fill=(255, 255, 255))
    draw.text((105, 1190), "Ragnar One  •  @ragnarplay1  •  ragnarplay.online", font=footer, fill=(174, 193, 208))
    img.save(path, "PNG", optimize=True)
    return path


def _scheduled_caption(slot):
    theme = _scheduled_theme(slot)
    return (
        theme["headline"] + "\n\n"
        + theme["support"] + "\n\n"
        + 'Comente "QUERO" e saiba mais.\n\n'
        + "#RagnarOne #Streaming #Futebol #FilmesESeries #Entretenimento"
    )


def _publish_scheduled_image(slot):
    image = _create_scheduled_post_image(slot)
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



def _schedule_times():
    # Política fixa do Ragnar One: três publicações diárias no horário de São Paulo.
    # Não depende de valores antigos persistidos no volume do Railway.
    desired_times = "09:00, 12:00, 18:00"
    try:
        now = time.time()
        with settings_db() as c:
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES('post_times',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (desired_times, now),
            )
            c.execute(
                "INSERT INTO settings(key,value,updated) VALUES('schedule_policy_version',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                ("2026-09-22_fixed_09-12-18", now),
            )
    except sqlite3.Error:
        pass
    return [(9, 0), (12, 0), (18, 0)]

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
        if done and done[0]:
            return
        attempt = c.execute("SELECT value FROM settings WHERE key=?", (attempt_key,)).fetchone()

    # Evita martelar a Meta em caso de erro temporário. Tenta novamente após 10 min.
    if attempt and attempt[0]:
        try:
            if time.time() - float(attempt[0]) < 10 * 60:
                return
        except (TypeError, ValueError):
            pass

    now_ts = time.time()
    with settings_db() as c:
        c.execute(
            "INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",
            (attempt_key, str(now_ts), now_ts),
        )

    print(f"SCHEDULE_IMAGE_ATTEMPT slot={slot:%Y-%m-%d_%H:%M}", flush=True)
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
