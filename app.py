"""Ragnar: atendimento inicial, sem publicação automática ou IA generativa."""
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
import panel

LOG = logging.getLogger('ragnar')
BRAND = json.loads(Path(__file__).with_name('brand.json').read_text())

def env(name):
    return os.environ.get(name, '').strip()

def blockers():
    missing = [k for k in ('META_APP_SECRET', 'WEBHOOK_VERIFY_TOKEN', 'INSTAGRAM_ACCESS_TOKEN', 'INSTAGRAM_ACCOUNT_ID', 'META_API_VERSION') if not env(k)]
    if not re.fullmatch(r'v\d+\.\d+', env('META_API_VERSION')):
        missing.append('META_API_VERSION_FORMAT')
    if env('WHATSAPP_CONFIRMED') != 'true':
        missing.append('WHATSAPP_CONFIRMED')
    if not env('RAILWAY_VOLUME_MOUNT_PATH'):
        missing.append('RAILWAY_VOLUME_MOUNT_PATH')
    return sorted(set(missing))

def active():
    return env('AUTOMATION_ENABLED') == 'true' and not blockers()

def db():
    root = Path(env('RAILWAY_VOLUME_MOUNT_PATH') or env('DATA_DIR') or './data')
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / 'ragnar.sqlite3', timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, recipient TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, error TEXT, message_id TEXT)')
    return c

def greeting():
    return panel.get_setting(
        'greeting_text',
        "Olá! 👋 Sou o Ragnar, assistente da Ragnar One. Vi que você comentou QUERO. Escolha uma opção abaixo:"
    )

def interactive_message():
    phone = panel.get_setting('whatsapp_number', env('WHATSAPP_NUMBER') or BRAND['whatsapp_number'])
    website = panel.get_setting('website', BRAND['website'])
    site_title = panel.get_setting('site_button_title', 'Acessar site')[:20]
    whatsapp_title = panel.get_setting('whatsapp_button_title', 'Falar no WhatsApp')[:20]
    whatsapp = 'https://wa.me/' + phone + '?text=' + urllib.parse.quote(
        'Olá! Vim pelo Instagram da Ragnar One.'
    )
    return {
        'attachment': {
            'type': 'template',
            'payload': {
                'template_type': 'button',
                'text': greeting(),
                'buttons': [
                    {'type': 'web_url', 'url': website, 'title': site_title},
                    {'type': 'web_url', 'url': whatsapp, 'title': whatsapp_title}
                ]
            }
        }
    }

def valid_signature(body, signature):
    secret = env('META_APP_SECRET')
    if not secret:
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def collect(payload):
    if payload.get('object') != 'instagram':
        return []
    jobs = []
    for entry in payload.get('entry', []):
        if str(entry.get('id', '')) != env('INSTAGRAM_ACCOUNT_ID'):
            continue
        for change in entry.get('changes', []):
            value = change.get('value', {})
            if change.get('field') != 'comments' or not value.get('id'):
                continue
            if str(value.get('from', {}).get('id', '')) == env('INSTAGRAM_ACCOUNT_ID'):
                continue
            text = value.get('text', '')
            if isinstance(text, str) and re.search(r'\bquero\b', text, re.I):
                cid = str(value['id'])
                jobs.append(('comment:' + cid, cid, 'comment', time.time()))
        # Mensagens recebidas no Direct não disparam a saudação automática.
        # Isso evita repetir a oferta quando a pessoa responde "TESTE", "oi" etc.
    return jobs

def enqueue(jobs):
    with db() as c:
        c.executemany('INSERT OR IGNORE INTO jobs (id,recipient,kind,created,status) VALUES (?,?,?,?,\'pending\')', jobs)

def send(recipient, kind):
    host = 'https://graph.instagram.com'
    account = env('INSTAGRAM_ACCOUNT_ID')
    if not account.isdigit():
        raise ValueError('account_id_format')
    if kind != 'comment':
        raise ValueError('unsupported_kind')
    body = {'recipient': {'comment_id': recipient}, 'message': interactive_message()}
    req = urllib.request.Request(f'{host}/{env("META_API_VERSION")}/{account}/messages', data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + env('INSTAGRAM_ACCESS_TOKEN'), 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
    if not result.get('message_id'):
        raise ValueError('missing_message_id')
    return str(result['message_id'])

def process_one():
    if not active():
        return
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        count = c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('sending','sent','uncertain','failed') AND created > ?", (time.time() - 86400,)).fetchone()[0]
        if count >= int(env('DAILY_ACTION_LIMIT') or '30'):
            return
        row = c.execute("SELECT id,recipient,kind,created FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
        if not row:
            return
        jid, recipient, kind, created = row
        # Limite conservador: não enviar respostas antigas após retomada.
        if time.time() - created > 23 * 3600:
            c.execute("UPDATE jobs SET status='expired' WHERE id=?", (jid,))
            return
        c.execute("UPDATE jobs SET status='sending' WHERE id=?", (jid,))
    try:
        message_id = send(recipient, kind)
        status, error = 'sent', None
    except urllib.error.HTTPError as exc:
        status, error, message_id = 'failed', f'HTTP_{exc.code}', None
    except Exception:
        # Não repetir cegamente: a Meta pode ter recebido o envio antes do timeout.
        status, error, message_id = 'uncertain', 'delivery_unknown', None
    with db() as c:
        c.execute('UPDATE jobs SET status=?,error=?,message_id=? WHERE id=?', (status, error, message_id, jid))
    LOG.info('delivery_status=%s', status)

_stop = threading.Event()
def worker():
    while not _stop.wait(1):
        try:
            process_one()
            panel.process_manual_image_post_once()
            panel.process_scheduled_posts_once()
            panel.process_test_post_once()
            panel.process_publication_once()
        except Exception:
            LOG.error('worker_iteration_failed')

def start_worker():
    with db() as c:
        c.execute("UPDATE jobs SET status='uncertain',error='restart_during_send' WHERE status='sending'")
    try:
        panel._process_globalplay_force_post_once()
    except Exception:
        LOG.exception("globalplay_force_post_startup_failed")
    threading.Thread(target=worker, daemon=True).start()

def application(environ, start_response):
    def reply(code, data, content_type='application/json'):
        body = (json.dumps(data, ensure_ascii=False) if content_type == 'application/json' else str(data)).encode()
        start_response(code, [('Content-Type', content_type + '; charset=utf-8'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
        return [body]
    path, method = environ.get('PATH_INFO', ''), environ.get('REQUEST_METHOD', 'GET')
    if path == '/media-public':
        return panel.handle_public_media(environ, start_response)
    if path == '/public-file':
        return panel.handle_public_file(environ, start_response)
    if path == '/run-test-post':
        return panel.handle_test_trigger(environ, start_response)
    if path == '/run-commercial-post':
        return panel.handle_commercial_trigger(environ, start_response)
    if (
        path == '/globalplay-force-post'
        or path.startswith('/globalplay-force-post/')
        or path == '/globalplay_force_post'
    ):
        return panel.handle_globalplay_force_post(environ, start_response)
    if path.startswith('/panel'):
        return panel.handle(environ, start_response)
    if path == '/healthz' and method == 'GET':
        return reply('200 OK', {'status': 'ok', 'agent': 'Ragnar'})
    if path == '/' and method == 'GET':
        return reply('200 OK', {'agent': 'Ragnar', 'instagram': '@ragnarplay1', 'automation_enabled': active()})
    if path == '/readyz' and method == 'GET':
        return reply('200 OK' if active() else '503 Service Unavailable', {'ready': active(), 'missing': blockers(), 'enabled': env('AUTOMATION_ENABLED') == 'true'})
    if path != '/webhook':
        return reply('404 Not Found', {'error': 'not_found'})
    if method == 'GET':
        q = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        token = q.get('hub.verify_token', [''])[0]
        if env('WEBHOOK_VERIFY_TOKEN') and q.get('hub.mode') == ['subscribe'] and hmac.compare_digest(token, env('WEBHOOK_VERIFY_TOKEN')):
            return reply('200 OK', q.get('hub.challenge', [''])[0], 'text/plain')
        return reply('403 Forbidden', {'error': 'verification_failed'})
    if method != 'POST':
        return reply('405 Method Not Allowed', {'error': 'method_not_allowed'})
    try:
        length = int(environ.get('CONTENT_LENGTH') or '0')
        if length < 1 or length > 1048576:
            return reply('413 Payload Too Large', {'error': 'invalid_size'})
        body = environ['wsgi.input'].read(length)
        if not valid_signature(body, environ.get('HTTP_X_HUB_SIGNATURE_256', '')):
            return reply('403 Forbidden', {'error': 'invalid_signature'})
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError()
        if not active():
            return reply('503 Service Unavailable', {'error': 'automation_not_ready'})
        jobs = collect(payload)
        enqueue(jobs)
    except (ValueError, TypeError, AttributeError, KeyError):
        return reply('400 Bad Request', {'error': 'invalid_payload'})
    except sqlite3.Error:
        return reply('503 Service Unavailable', {'error': 'storage_unavailable'})
    return reply('200 OK', {'received': True})
