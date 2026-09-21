"""Painel web do Ragnar: configuracao e biblioteca de videos."""
import cgi
import hashlib
import hmac
import html
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path

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
    "post_times": "09:00, 15:00, 21:00",
}

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
:root{{--bg:#071018;--card:#101c27;--card2:#142433;--text:#f4f7fa;--muted:#9fb0bf;--red:#e22d3e;--line:#263746;--green:#20b26b}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(135deg,#071018,#0a1621 55%,#10161b);color:var(--text);font-family:Inter,Arial,sans-serif}}
.wrap{{max-width:1180px;margin:auto;padding:24px}} .top{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}}
.brand{{font-weight:900;font-size:28px;letter-spacing:.6px}} .brand span{{color:var(--red)}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .card{{background:rgba(16,28,39,.96);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 18px 50px #0005}}
.full{{grid-column:1/-1}} h1,h2{{margin-top:0}} label{{display:block;font-weight:700;margin:13px 0 6px}}
input,textarea,select{{width:100%;border:1px solid #314657;background:#09131c;color:white;border-radius:11px;padding:12px;font:inherit}}
textarea{{min-height:96px;resize:vertical}} button,.btn{{display:inline-block;border:0;border-radius:11px;padding:11px 16px;background:var(--red);color:white;font-weight:800;text-decoration:none;cursor:pointer}}
.btn.secondary{{background:#203242}} .notice{{background:#113824;border:1px solid #226d45;color:#caffdf;padding:12px 14px;border-radius:11px;margin-bottom:16px}}
.video{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border-bottom:1px solid var(--line)}} .video:last-child{{border-bottom:0}}
.small{{font-size:13px;color:var(--muted)}} code{{background:#071018;padding:2px 6px;border-radius:6px}}
.login{{max-width:430px;margin:12vh auto}} .status{{display:inline-block;padding:5px 9px;border-radius:99px;background:#103a27;color:#baffd7;font-size:12px;font-weight:800}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}} .full{{grid-column:auto}} .top{{align-items:flex-start;flex-direction:column}}}}
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
    return stem[:100] or "video.mp4"

def _media_rows():
    rows = []
    for folder, kind in ((data_root() / "uploads", "Original"), (data_root() / "cuts", "Corte")):
        folder.mkdir(parents=True, exist_ok=True)
        for p in sorted(folder.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                rows.append((p, kind))
    return rows[:80]

def _dashboard(notice=""):
    s = {k: get_setting(k, v) for k, v in DEFAULTS.items()}
    rows = _media_rows()
    videos = ""
    for p, kind in rows:
        mb = p.stat().st_size / (1024 * 1024)
        videos += f"""<div class="video">
<div><strong>{html.escape(p.name)}</strong><div class="small">{kind} · {mb:.1f} MB</div></div>
"""
        if kind == "Original":
            videos += f"""<form method="post" action="/panel/cut">
<input type="hidden" name="filename" value="{html.escape(p.name)}">
<input type="hidden" name="start" value="0"><input type="hidden" name="duration" value="30">
<button type="submit">Gerar corte 30s</button></form>"""
        else:
            videos += '<span class="status">PRONTO</span>'
        videos += "</div>"
    if not videos:
        videos = '<p class="muted">Nenhum vídeo enviado ainda.</p>'

    content = f"""
<div class="top">
<div><div class="brand">RAGNAR <span>ONE</span></div><div class="muted">Painel do agente</div></div>
<div><span class="status">ONLINE</span> &nbsp; <a class="btn secondary" href="/panel/logout">Sair</a></div>
</div>

<div class="grid">
<section class="card">
<h2>Enviar vídeo</h2>
<p class="muted">MP4, MOV, WebM ou M4V. O arquivo fica salvo no volume persistente do Ragnar.</p>
<form method="post" action="/panel/upload" enctype="multipart/form-data">
<label>Escolha o vídeo</label>
<input type="file" name="video" accept="video/mp4,video/quicktime,video/webm,.m4v" required>
<p><button type="submit">Enviar vídeo</button></p>
</form>
</section>

<section class="card">
<h2>Programação de conteúdo</h2>
<p class="muted">Esses dados já ficam salvos para o módulo de publicação automática.</p>
<form method="post" action="/panel/settings">
<label>Postagens por dia</label>
<input name="posts_per_day" type="number" min="1" max="10" value="{html.escape(s['posts_per_day'])}">
<label>Horários</label>
<input name="post_times" value="{html.escape(s['post_times'])}" placeholder="09:00, 15:00, 21:00">
<label>Frase obrigatória nos banners e cortes</label>
<input name="banner_cta" value="{html.escape(s['banner_cta'])}">
<p><button type="submit">Salvar programação</button></p>
</form>
</section>

<section class="card full">
<h2>Configurar Ragnar</h2>
<p class="muted">Seu amigo pode mudar frases, links e as regras do agente sem entrar no GitHub.</p>
<form method="post" action="/panel/settings">
<div class="grid">
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
<h2>Biblioteca de vídeos</h2>
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
    if not item or not getattr(item, "filename", ""):
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

def _generate_cut(filename, start, duration):
    source = data_root() / "uploads" / _safe_name(filename)
    if not source.exists() or source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Vídeo original não encontrado")
    start_n = max(0.0, min(float(start or 0), 36000.0))
    duration_n = max(5.0, min(float(duration or 30), 90.0))
    cuts = data_root() / "cuts"
    cuts.mkdir(parents=True, exist_ok=True)
    output = cuts / (source.stem + f"-corte-{int(start_n)}s-{int(duration_n)}s.mp4")
    cta_file = data_root() / "cta.txt"
    cta_file.write_text(get_setting("banner_cta", DEFAULTS["banner_cta"]), encoding="utf-8")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"drawtext=fontfile={font}:textfile={cta_file}:"
        "fontcolor=white:fontsize=48:borderw=4:bordercolor=black:"
        "box=1:boxcolor=black@0.38:boxborderw=22:"
        "x=(w-text_w)/2:y=h-190"
    )
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_n), "-i", str(source),
        "-t", str(duration_n), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=240)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[-500:]
        raise RuntimeError("FFmpeg não conseguiu gerar o corte: " + detail)
    return output.name

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

    if path == "/panel/settings" and method == "POST":
        values = _parse_urlencoded(environ)
        save_settings(values)
        return _response(start_response, "200 OK", _dashboard("Configurações salvas. O Ragnar já usará as novas frases no atendimento."))

    if path == "/panel/upload" and method == "POST":
        try:
            name = _upload(environ)
            return _response(start_response, "200 OK", _dashboard("Vídeo enviado: " + name))
        except Exception as exc:
            return _response(start_response, "400 Bad Request", _dashboard(str(exc)))

    if path == "/panel/cut" and method == "POST":
        try:
            values = _parse_urlencoded(environ)
            name = _generate_cut(values.get("filename", ""), values.get("start", "0"), values.get("duration", "30"))
            return _response(start_response, "200 OK", _dashboard("Corte vertical gerado: " + name))
        except Exception as exc:
            return _response(start_response, "400 Bad Request", _dashboard(str(exc)))

    return _response(start_response, "404 Not Found", "Página não encontrada", "text/plain; charset=utf-8")
