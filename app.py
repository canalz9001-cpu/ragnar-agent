"""Ragnar One: atendimento, publicação automática e Odin para qualificação de leads."""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import odin
import panel

LOG = logging.getLogger("ragnar")
BRAND = json.loads(Path(__file__).with_name("brand.json").read_text())


def env(name):
    return os.environ.get(name, "").strip()


def blockers():
    missing = [
        k for k in (
            "META_APP_SECRET",
            "WEBHOOK_VERIFY_TOKEN",
            "INSTAGRAM_ACCESS_TOKEN",
            "INSTAGRAM_ACCOUNT_ID",
            "META_API_VERSION",
        ) if not env(k)
    ]
    if not re.fullmatch(r"v\d+\.\d+", env("META_API_VERSION")):
        missing.append("META_API_VERSION_FORMAT")
    if env("WHATSAPP_CONFIRMED") != "true":
        missing.append("WHATSAPP_CONFIRMED")
    if not env("RAILWAY_VOLUME_MOUNT_PATH"):
        missing.append("RAILWAY_VOLUME_MOUNT_PATH")
    return sorted(set(missing))


def active():
    return env("AUTOMATION_ENABLED") == "true" and not blockers()


def _ensure_column(connection, table, column, definition):
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def db():
    root = Path(env("RAILWAY_VOLUME_MOUNT_PATH") or env("DATA_DIR") or "./data")
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "ragnar.sqlite3", timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            recipient TEXT NOT NULL,
            kind TEXT NOT NULL,
            created REAL NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            message_id TEXT,
            body TEXT,
            username TEXT,
            instagram_user_id TEXT
        )"""
    )
    _ensure_column(c, "jobs", "body", "TEXT")
    _ensure_column(c, "jobs", "username", "TEXT")
    _ensure_column(c, "jobs", "instagram_user_id", "TEXT")
    odin.ensure_schema(c)
    return c


def greeting():
    return panel.get_setting(
        "greeting_text",
        "Olá! 👋 Sou o Ragnar, assistente da Ragnar One. Vi que você comentou QUERO. Escolha uma opção abaixo:",
    )


def interactive_message():
    phone = panel.get_setting("whatsapp_number", env("WHATSAPP_NUMBER") or BRAND["whatsapp_number"])
    website = panel.get_setting("website", BRAND["website"])
    site_title = panel.get_setting("site_button_title", "Acessar site")[:20]
    whatsapp_title = panel.get_setting("whatsapp_button_title", "Falar no WhatsApp")[:20]
    whatsapp = "https://wa.me/" + phone + "?text=" + urllib.parse.quote(
        "Olá! Vim pelo Instagram da Ragnar One."
    )
    return {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": greeting(),
                "buttons": [
                    {"type": "web_url", "url": website, "title": site_title},
                    {"type": "web_url", "url": whatsapp, "title": whatsapp_title},
                ],
            },
        }
    }


def valid_signature(body, signature):
    secret = env("META_APP_SECRET")
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def collect(payload):
    if payload.get("object") != "instagram":
        return []

    jobs = []
    account_id = env("INSTAGRAM_ACCOUNT_ID")

    for entry in payload.get("entry", []):
        if str(entry.get("id", "")) != account_id:
            continue

        # Comentários com QUERO: resposta privada + entrada imediata no Odin.
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") != "comments" or not value.get("id"):
                continue
            author = value.get("from", {}) or {}
            author_id = str(author.get("id", "") or "")
            if author_id == account_id:
                continue
            text = value.get("text", "")
            if isinstance(text, str) and re.search(r"\bquero\b", text, re.I):
                cid = str(value["id"])
                jobs.append((
                    "comment:" + cid,
                    cid,
                    "comment",
                    time.time(),
                    text,
                    str(author.get("username", "") or ""),
                    author_id,
                ))

        # Direct: Odin continua a conversa, qualifica e move o lead pelo funil.
        if odin.enabled():
            for messaging in entry.get("messaging", []):
                sender = messaging.get("sender", {}) or {}
                sender_id = str(sender.get("id", "") or "")
                message = messaging.get("message", {}) or {}
                message_id = str(message.get("mid", "") or "")
                text = message.get("text", "")
                if (
                    not sender_id
                    or sender_id == account_id
                    or not message_id
                    or not isinstance(text, str)
                    or not text.strip()
                    or message.get("is_echo")
                ):
                    continue
                jobs.append((
                    "dm:" + message_id,
                    sender_id,
                    "dm",
                    time.time(),
                    text.strip(),
                    str(sender.get("username", "") or ""),
                    sender_id,
                ))

    return jobs


def enqueue(jobs):
    if not jobs:
        return
    with db() as c:
        c.executemany(
            """INSERT OR IGNORE INTO jobs
               (id,recipient,kind,created,status,body,username,instagram_user_id)
               VALUES (?,?,?,?, 'pending',?,?,?)""",
            jobs,
        )


def _send_graph_message(recipient, message):
    host = "https://graph.instagram.com"
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account.isdigit():
        raise ValueError("account_id_format")
    req = urllib.request.Request(
        f"{host}/{env('META_API_VERSION')}/{account}/messages",
        data=json.dumps({
            "recipient": {"id": recipient},
            "message": {"text": message},
        }).encode(),
        headers={
            "Authorization": "Bearer " + env("INSTAGRAM_ACCESS_TOKEN"),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
    if not result.get("message_id"):
        raise ValueError("missing_message_id")
    return str(result["message_id"])


def send_comment_private_reply(comment_id):
    host = "https://graph.instagram.com"
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account.isdigit():
        raise ValueError("account_id_format")
    body = {
        "recipient": {"comment_id": comment_id},
        "message": interactive_message(),
    }
    req = urllib.request.Request(
        f"{host}/{env('META_API_VERSION')}/{account}/messages",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + env("INSTAGRAM_ACCESS_TOKEN"),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
    if not result.get("message_id"):
        raise ValueError("missing_message_id")
    return str(result["message_id"])


def _whatsapp_url():
    phone = panel.get_setting("whatsapp_number", env("WHATSAPP_NUMBER") or BRAND["whatsapp_number"])
    return "https://wa.me/" + re.sub(r"\D", "", str(phone))


def process_one():
    if not active():
        return

    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        count = c.execute(
            """SELECT COUNT(*) FROM jobs
               WHERE status IN ('sending','sent','uncertain','failed') AND created > ?""",
            (time.time() - 86400,),
        ).fetchone()[0]
        if count >= int(env("DAILY_ACTION_LIMIT") or "30"):
            return

        row = c.execute(
            """SELECT id,recipient,kind,created,COALESCE(body,''),COALESCE(username,''),
                      COALESCE(instagram_user_id,'')
               FROM jobs WHERE status='pending' ORDER BY created LIMIT 1"""
        ).fetchone()
        if not row:
            return

        jid, recipient, kind, created, body, username, instagram_user_id = row
        if time.time() - created > 23 * 3600:
            c.execute("UPDATE jobs SET status='expired' WHERE id=?", (jid,))
            return
        c.execute("UPDATE jobs SET status='sending' WHERE id=?", (jid,))

    outbound_text = ""
    try:
        if kind == "comment":
            with db() as c:
                odin.capture_lead(
                    c,
                    instagram_user_id or recipient,
                    username,
                    body,
                    trigger_keyword="QUERO",
                    source="comment",
                )
            message_id = send_comment_private_reply(recipient)
            outbound_text = greeting()

        elif kind == "dm":
            with db() as c:
                outbound_text = odin.qualification_reply(
                    c,
                    instagram_user_id or recipient,
                    username,
                    body,
                    panel.get_setting("website", BRAND["website"]),
                    _whatsapp_url(),
                )
            if not outbound_text:
                message_id = None
                status, error = "suppressed", "automation_loop_guard"
            else:
                message_id = _send_graph_message(recipient, outbound_text)
                status, error = "sent", None

        else:
            raise ValueError("unsupported_kind")

        if kind != "dm":
            status, error = "sent", None

    except urllib.error.HTTPError as exc:
        status, error, message_id = "failed", f"HTTP_{exc.code}", None
    except Exception:
        LOG.exception("odin_delivery_failed kind=%s", kind)
        status, error, message_id = "uncertain", "delivery_unknown", None

    with db() as c:
        c.execute(
            "UPDATE jobs SET status=?,error=?,message_id=? WHERE id=?",
            (status, error, message_id, jid),
        )
        if status == "sent" and outbound_text:
            odin.record_outbound(c, instagram_user_id or recipient, outbound_text)

    LOG.info("delivery_status=%s kind=%s", status, kind)


_stop = threading.Event()
_worker_threads = {}
_worker_lock = threading.Lock()


def message_worker():
    while not _stop.wait(1):
        try:
            process_one()
        except Exception:
            LOG.exception("message_worker_iteration_failed")


def posting_worker():
    print("POSTING_WORKER_LOOP_STARTED", flush=True)
    try:
        local_now = panel.datetime.now(panel.ZoneInfo("America/Sao_Paulo"))
        schedule = panel._schedule_times()
        print(
            "POSTING_SCHEDULE_DIAG local="
            + local_now.strftime("%Y-%m-%d %H:%M:%S")
            + " times="
            + ",".join(f"{h:02d}:{m:02d}" for h, m in schedule),
            flush=True,
        )
    except Exception as exc:
        print(
            "POSTING_SCHEDULE_DIAG_ERROR type="
            + type(exc).__name__
            + " error="
            + str(exc)[:700],
            flush=True,
        )
    while not _stop.wait(2):
        tasks = (
            ("manual", panel.process_manual_image_post_once),
            ("scheduled", panel.process_scheduled_posts_once),
            ("test", panel.process_test_post_once),
            ("publication", panel.process_publication_once),
        )
        for name, task in tasks:
            try:
                task()
            except Exception as exc:
                print(
                    "POSTING_WORKER_ERROR task=" + name
                    + " type=" + type(exc).__name__
                    + " error=" + str(exc)[:700],
                    flush=True,
                )
                LOG.exception("posting_worker_task_failed task=%s", name)


def start_worker():
    global _worker_threads
    with _worker_lock:
        alive = {name: thread for name, thread in _worker_threads.items() if thread.is_alive()}
        _worker_threads = alive

        if "message" not in _worker_threads:
            with db() as c:
                c.execute(
                    "UPDATE jobs SET status='uncertain',error='restart_during_send' WHERE status='sending'"
                )
            thread = threading.Thread(
                target=message_worker, daemon=True, name="ragnar-message-worker"
            )
            thread.start()
            _worker_threads["message"] = thread
            print("MESSAGE_WORKER_STARTED ODIN_ENABLED=" + str(odin.enabled()), flush=True)

        if "posting" not in _worker_threads:
            thread = threading.Thread(
                target=posting_worker, daemon=True, name="ragnar-posting-worker"
            )
            thread.start()
            _worker_threads["posting"] = thread
            print("POSTING_WORKER_STARTED", flush=True)


start_worker()


def _nexus_authorized(environ):
    expected = env("NEXUS_AGENT_TOKEN")
    provided = environ.get("HTTP_AUTHORIZATION", "")
    return bool(
        expected
        and provided.startswith("Bearer ")
        and hmac.compare_digest(provided[7:], expected)
    )



def _nexus_insight_value(payload, metric):
    for row in payload.get("data") or []:
        if str(row.get("name") or "") != metric:
            continue
        values = row.get("values") or []
        raw = values[0].get("value") if values and isinstance(values[0], dict) else None
        if raw is None and isinstance(row.get("total_value"), dict):
            raw = row["total_value"].get("value")
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _nexus_instagram_snapshot(limit=25):
    account = env("INSTAGRAM_ACCOUNT_ID")
    if not account or not env("INSTAGRAM_ACCESS_TOKEN") or not env("META_API_VERSION"):
        raise RuntimeError("instagram_not_configured")

    safe_limit = max(1, min(25, int(limit or 25)))
    profile_error = ""
    username = ""
    followers_count = 0
    media_count = 0
    try:
        profile = panel._graph_request(
            f"{account}?fields=username,followers_count,media_count",
            "GET",
        )
        username = str(profile.get("username") or "")
        followers_count = max(0, int(profile.get("followers_count") or 0))
        media_count = max(0, int(profile.get("media_count") or 0))
    except Exception as exc:
        profile_error = str(exc)[:240]

    media = panel._graph_request(
        f"{account}/media?fields=id,caption,media_type,timestamp,like_count,comments_count,permalink&limit={safe_limit}",
        "GET",
    )
    items = []
    insight_errors = []
    metric_sets = (
        "reach,views,shares,total_interactions,saved",
        "reach,views,shares,total_interactions",
        "reach,views",
        "reach",
    )

    for raw_item in media.get("data") or []:
        media_id = str(raw_item.get("id") or "")
        insight = {}
        last_error = ""
        used_metrics = []
        if media_id:
            for metrics in metric_sets:
                try:
                    payload = panel._graph_request(
                        f"{media_id}/insights?metric={urllib.parse.quote(metrics)}",
                        "GET",
                    )
                    insight = payload
                    used_metrics = metrics.split(",")
                    last_error = ""
                    break
                except Exception as exc:
                    last_error = str(exc)[:240]

        item = {
            "id": media_id,
            "caption": str(raw_item.get("caption") or "")[:2200],
            "timestamp": raw_item.get("timestamp"),
            "mediaType": str(raw_item.get("media_type") or ""),
            "likeCount": max(0, int(raw_item.get("like_count") or 0)),
            "commentsCount": max(0, int(raw_item.get("comments_count") or 0)),
            "permalink": str(raw_item.get("permalink") or ""),
            "reach": _nexus_insight_value(insight, "reach"),
            "views": _nexus_insight_value(insight, "views"),
            "saved": _nexus_insight_value(insight, "saved"),
            "shares": _nexus_insight_value(insight, "shares"),
            "totalInteractions": _nexus_insight_value(insight, "total_interactions"),
            "insightMetrics": used_metrics,
            "insightError": last_error,
        }
        if last_error:
            insight_errors.append({"id": media_id, "error": last_error})
        items.append(item)

    return {
        "ok": True,
        "source": "ragnar-instagram-api",
        "username": username,
        "followersCount": followers_count,
        "mediaCount": media_count,
        "items": items,
        "insightErrors": insight_errors[:10],
        "profileError": profile_error,
    }


def _read_nexus_json(environ, max_bytes):
    length = int(environ.get("CONTENT_LENGTH") or "0")
    if length < 1 or length > max_bytes:
        raise ValueError("invalid_size")
    raw = environ["wsgi.input"].read(length)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    return payload


def _openai_json_request(path, payload, timeout=180):
    api_key = env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("openai_not_configured")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/" + path.lstrip("/"),
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Ragnar-NEXUS-Bridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1200]
        raise RuntimeError("openai_http_" + str(exc.code) + ":" + detail) from exc


def _openai_transcription_request(audio_bytes, filename="audio.mp3", timeout=600):
    api_key = env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("openai_not_configured")
    boundary = "----RagnarNexus" + hashlib.sha256(
        (str(time.time()) + filename).encode("utf-8")
    ).hexdigest()[:24]
    parts = []

    def field(name, value):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append((
            'Content-Disposition: form-data; name="' + name + '"\r\n\r\n'
        ).encode())
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    field("model", "whisper-1")
    field("response_format", "verbose_json")
    field("timestamp_granularities[]", "segment")

    parts.append(("--" + boundary + "\r\n").encode())
    safe_name = re.sub(r'[^A-Za-z0-9._-]+', "_", filename or "audio.mp3")[:120]
    parts.append((
        'Content-Disposition: form-data; name="file"; filename="' + safe_name + '"\r\n'
        "Content-Type: audio/mpeg\r\n\r\n"
    ).encode())
    parts.append(audio_bytes)
    parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)

    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Accept": "application/json",
            "User-Agent": "Ragnar-NEXUS-Bridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1200]
        raise RuntimeError("openai_transcription_http_" + str(exc.code) + ":" + detail) from exc



def application(environ, start_response):
    def reply(code, data, content_type="application/json"):
        body = (
            json.dumps(data, ensure_ascii=False)
            if content_type == "application/json"
            else str(data)
        ).encode()
        start_response(code, [
            ("Content-Type", content_type + "; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ])
        return [body]

    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "GET")

    if path == "/nexus/openai/responses" and method == "POST":
        if not _nexus_authorized(environ):
            return reply("401 Unauthorized", {"error": "unauthorized"})
        try:
            payload = _read_nexus_json(environ, 512 * 1024)
            model = env("OPENAI_MODEL") or "gpt-5.6-luna"
            allowed = {
                "model": model,
                "input": payload.get("input", ""),
                "max_output_tokens": min(6000, max(64, int(payload.get("max_output_tokens") or 1200))),
            }
            if payload.get("instructions"):
                allowed["instructions"] = str(payload.get("instructions"))[:16000]
            result = _openai_json_request("responses", allowed, timeout=180)
            return reply("200 OK", result)
        except ValueError as exc:
            code = "413 Payload Too Large" if str(exc) == "invalid_size" else "400 Bad Request"
            return reply(code, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("nexus_openai_responses_failed")
            return reply("502 Bad Gateway", {"error": "openai_bridge_failed", "detail": str(exc)[:300]})

    if path == "/nexus/openai/transcriptions" and method == "POST":
        if not _nexus_authorized(environ):
            return reply("401 Unauthorized", {"error": "unauthorized"})
        try:
            payload = _read_nexus_json(environ, 38 * 1024 * 1024)
            encoded = str(payload.get("audio_base64") or "")
            if not encoded:
                return reply("400 Bad Request", {"error": "audio_required"})
            audio = base64.b64decode(encoded, validate=True)
            if not audio or len(audio) > 25 * 1024 * 1024:
                return reply("413 Payload Too Large", {"error": "audio_too_large"})
            result = _openai_transcription_request(
                audio,
                str(payload.get("filename") or "audio.mp3")[:120],
                timeout=600,
            )
            return reply("200 OK", result)
        except ValueError as exc:
            code = "413 Payload Too Large" if str(exc) == "invalid_size" else "400 Bad Request"
            return reply(code, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("nexus_openai_transcription_failed")
            return reply("502 Bad Gateway", {"error": "openai_bridge_failed", "detail": str(exc)[:300]})

    if path == "/nexus/instagram/insights" and method == "GET":
        if not _nexus_authorized(environ):
            return reply("401 Unauthorized", {"error": "unauthorized"})
        try:
            return reply("200 OK", _nexus_instagram_snapshot(25))
        except Exception as exc:
            LOG.exception("nexus_instagram_insights_failed")
            return reply(
                "502 Bad Gateway",
                {"error": "instagram_insights_unavailable", "detail": str(exc)[:300]},
            )

    if path == "/nexus/status" and method == "GET":
        expected = env("NEXUS_AGENT_TOKEN")
        provided = environ.get("HTTP_AUTHORIZATION", "")
        if not expected or not provided.startswith("Bearer ") or not hmac.compare_digest(provided[7:], expected):
            return reply("401 Unauthorized", {"error": "unauthorized"})
        return reply("200 OK", panel.nexus_status_snapshot())
    if path == "/nexus/leads" and method == "GET":
        expected = env("NEXUS_AGENT_TOKEN")
        provided = environ.get("HTTP_AUTHORIZATION", "")
        if not expected or not provided.startswith("Bearer ") or not hmac.compare_digest(provided[7:], expected):
            return reply("401 Unauthorized", {"error": "unauthorized"})
        with db() as c:
            return reply("200 OK", odin.nexus_leads_snapshot(c))
    if path == "/media-public":
        return panel.handle_public_media(environ, start_response)
    if path == "/public-file":
        return panel.handle_public_file(environ, start_response)
    if path == "/run-test-post":
        return panel.handle_test_trigger(environ, start_response)
    if path == "/run-commercial-post":
        return panel.handle_commercial_trigger(environ, start_response)
    if path in ("/odin", "/odin/"):
        with db() as c:
            return odin.dashboard(environ, start_response, c)
    if path.startswith("/panel"):
        return panel.handle(environ, start_response)

    if path == "/healthz" and method == "GET":
        return reply("200 OK", {
            "status": "ok",
            "agent": "Ragnar",
            "odin_enabled": odin.enabled(),
        })
    if path == "/" and method == "GET":
        return reply("200 OK", {
            "agent": "Ragnar",
            "instagram": "@ragnarplay1",
            "automation_enabled": active(),
            "odin_enabled": odin.enabled(),
        })
    if path == "/readyz" and method == "GET":
        return reply(
            "200 OK" if active() else "503 Service Unavailable",
            {
                "ready": active(),
                "missing": blockers(),
                "enabled": env("AUTOMATION_ENABLED") == "true",
                "odin_enabled": odin.enabled(),
            },
        )

    if path != "/webhook":
        return reply("404 Not Found", {"error": "not_found"})

    if method == "GET":
        q = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
        token = q.get("hub.verify_token", [""])[0]
        if (
            env("WEBHOOK_VERIFY_TOKEN")
            and q.get("hub.mode") == ["subscribe"]
            and hmac.compare_digest(token, env("WEBHOOK_VERIFY_TOKEN"))
        ):
            return reply("200 OK", q.get("hub.challenge", [""])[0], "text/plain")
        return reply("403 Forbidden", {"error": "verification_failed"})

    if method != "POST":
        return reply("405 Method Not Allowed", {"error": "method_not_allowed"})

    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        if length < 1 or length > 1048576:
            return reply("413 Payload Too Large", {"error": "invalid_size"})
        body = environ["wsgi.input"].read(length)
        if not valid_signature(body, environ.get("HTTP_X_HUB_SIGNATURE_256", "")):
            return reply("403 Forbidden", {"error": "invalid_signature"})
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError()
        if not active():
            return reply("503 Service Unavailable", {"error": "automation_not_ready"})
        enqueue(collect(payload))
    except (ValueError, TypeError, AttributeError, KeyError):
        return reply("400 Bad Request", {"error": "invalid_payload"})
    except sqlite3.Error:
        LOG.exception("storage_unavailable")
        return reply("503 Service Unavailable", {"error": "storage_unavailable"})

    return reply("200 OK", {"received": True})
