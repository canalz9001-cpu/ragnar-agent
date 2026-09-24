"""Fresh-start guard for Ragnar Instagram learning.

Loaded automatically by Python's site module. It makes 24/09/2026 a one-day
schedule experiment (09:00, 12:00, 16:00), purges cached learning older than
the reset, and hides older Instagram media from future learning/audit calls.
"""
import io
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RESET_CUTOFF_ISO = "2026-09-24T11:00:00+00:00"  # 08:00 America/Sao_Paulo
RESET_CUTOFF = datetime.fromisoformat(RESET_CUTOFF_ISO).timestamp()
TEST_DAY = "2026-09-24"


def _sao_paulo_day():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d")


# Exceção SOMENTE para hoje. Amanhã o Ragnar volta sozinho ao modo adaptativo.
if _sao_paulo_day() == TEST_DAY:
    os.environ["ADAPTIVE_POST_TIMES"] = "false"
    os.environ["AUTO_POST_TIMES"] = "09:00,12:00,16:00"


def _reset_cached_learning():
    root = Path(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("DATA_DIR") or "./data")
    db_path = root / "ragnar.sqlite3"
    if not db_path.exists():
        return
    try:
        with sqlite3.connect(db_path, timeout=5) as connection:
            try:
                connection.execute(
                    "DELETE FROM content_intelligence WHERE created < ?",
                    (RESET_CUTOFF,),
                )
            except sqlite3.Error:
                pass
            try:
                connection.execute(
                    "DELETE FROM settings WHERE key LIKE 'adaptive_post_times_%' AND updated < ?",
                    (RESET_CUTOFF,),
                )
            except sqlite3.Error:
                pass
    except sqlite3.Error:
        pass


_reset_cached_learning()
_original_urlopen = urllib.request.urlopen


class _BufferedResponse(io.BytesIO):
    def __init__(self, body, status=200, headers=None, url=""):
        super().__init__(body)
        self.status = status
        self.headers = headers
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers


def _target_url(target):
    if isinstance(target, urllib.request.Request):
        return target.full_url
    return str(target)


def _fresh_urlopen(target, *args, **kwargs):
    response = _original_urlopen(target, *args, **kwargs)
    url = _target_url(target)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    fields = ",".join(query.get("fields", []))
    is_media_list = (
        parsed.hostname == "graph.instagram.com"
        and parsed.path.rstrip("/").endswith("/media")
        and "timestamp" in fields
    )
    if not is_media_list:
        return response

    raw = response.read()
    status = getattr(response, "status", 200)
    headers = getattr(response, "headers", None)
    try:
        response.close()
    except Exception:
        pass

    try:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            filtered = []
            for item in payload["data"]:
                try:
                    stamp = str((item or {}).get("timestamp") or "").replace("Z", "+00:00")
                    ts = datetime.fromisoformat(stamp).timestamp()
                except Exception:
                    continue
                if ts >= RESET_CUTOFF:
                    filtered.append(item)
            payload["data"] = filtered
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        pass

    return _BufferedResponse(raw, status=status, headers=headers, url=url)


urllib.request.urlopen = _fresh_urlopen

print(
    "FRESH_START cutoff=2026-09-24T08:00:00-03:00 schedule="
    + ("09:00,12:00,16:00" if _sao_paulo_day() == TEST_DAY else "adaptive"),
    flush=True,
)
