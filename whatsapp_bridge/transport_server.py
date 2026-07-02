#!/usr/bin/env python3
import hashlib
import json
import mimetypes
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


HOST = os.environ.get("CANOPY_TRANSPORT_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("CANOPY_TRANSPORT_PORT", "8091").strip())
DB_PATH = os.environ.get("CANOPY_TRANSPORT_DB", "./canopy_whatsapp_transport.sqlite").strip()
ENVIRONMENT = os.environ.get("CANOPY_ENV", "staging").strip()
GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
WEBHOOK_VERIFY_TOKEN = os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip()
INTERNAL_TOKEN = os.environ.get("CANOPY_TRANSPORT_INTERNAL_TOKEN", "").strip()
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            wa_id TEXT PRIMARY KEY,
            profile_name TEXT,
            last_inbound_at TEXT,
            last_outbound_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            wa_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            message_type TEXT NOT NULL,
            text TEXT,
            raw_json TEXT NOT NULL,
            meta_message_id TEXT,
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_attempts (
            idempotency_key TEXT PRIMARY KEY,
            wa_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT,
            error TEXT,
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS message_statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meta_message_id TEXT NOT NULL,
            wa_id TEXT,
            status TEXT NOT NULL,
            status_timestamp TEXT,
            raw_json TEXT NOT NULL,
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_statuses_meta_message_id
        ON message_statuses(meta_message_id)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_statuses_wa_id
        ON message_statuses(wa_id)
        """
    )
    return con


def json_response(handler, code, data):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def resolve_asset(request_path):
    rel = unquote(request_path[len("/assets/") :])
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    asset_path = (ASSETS_DIR / rel).resolve()
    try:
        asset_path.relative_to(ASSETS_DIR.resolve())
    except ValueError:
        return None
    if not asset_path.is_file():
        return None
    return asset_path


def serve_static_asset(handler, request_path, head_only=False):
    asset_path = resolve_asset(request_path)
    if not asset_path:
        return json_response(handler, 404, {"ok": False, "error": "asset not found"})

    content_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
    size = asset_path.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.end_headers()
    if not head_only:
        with asset_path.open("rb") as src:
            handler.wfile.write(src.read())
    return None


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


def safe_error(exc):
    text = str(exc)
    if "Invalid header value" in text or "Authorization" in text or "Bearer " in text:
        return "Meta request failed before send: invalid authorization header"
    return text


def require_internal_auth(handler):
    if not INTERNAL_TOKEN:
        return False, (500, {"ok": False, "error": "CANOPY_TRANSPORT_INTERNAL_TOKEN is not set"})
    expected = f"Bearer {INTERNAL_TOKEN}"
    if handler.headers.get("Authorization", "") != expected:
        return False, (401, {"ok": False, "error": "invalid internal token"})
    return True, None


def extract_messages(payload):
    messages = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            contacts = {c.get("wa_id"): c for c in value.get("contacts", []) or []}
            for msg in value.get("messages", []) or []:
                wa_id = msg.get("from", "")
                contact = contacts.get(wa_id) or {}
                profile = contact.get("profile", {}) or {}
                message_type = msg.get("type", "unknown")
                text = ""
                if message_type == "text":
                    text = ((msg.get("text") or {}).get("body") or "").strip()
                elif message_type:
                    text = f"[{message_type} message]"
                messages.append(
                    {
                        "id": msg.get("id") or f"{wa_id}:{msg.get('timestamp', utc_now())}",
                        "wa_id": wa_id,
                        "profile_name": profile.get("name", ""),
                        "message_type": message_type,
                        "text": text,
                        "raw": msg,
                    }
                )
    return messages


def extract_statuses(payload):
    statuses = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for status in value.get("statuses", []) or []:
                statuses.append(
                    {
                        "meta_message_id": status.get("id", ""),
                        "wa_id": status.get("recipient_id", ""),
                        "status": status.get("status", "unknown"),
                        "timestamp": status.get("timestamp", ""),
                        "raw": status,
                    }
                )
    return statuses


def store_status_payload(payload):
    con = db()
    now = utc_now()
    stored = []
    for status in extract_statuses(payload):
        if not status["meta_message_id"]:
            continue
        con.execute(
            """
            INSERT INTO message_statuses
              (meta_message_id, wa_id, status, status_timestamp, raw_json, environment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status["meta_message_id"],
                status["wa_id"],
                status["status"],
                status["timestamp"],
                json.dumps(status["raw"], ensure_ascii=False),
                ENVIRONMENT,
                now,
            ),
        )
        stored.append(
            {
                "meta_message_id": status["meta_message_id"],
                "wa_id": status["wa_id"],
                "status": status["status"],
            }
        )
    con.commit()
    con.close()
    return stored


def store_inbound_payload(payload):
    con = db()
    now = utc_now()
    con.execute(
        "INSERT INTO webhook_events(raw_json, created_at) VALUES (?, ?)",
        (json.dumps(payload, ensure_ascii=False), now),
    )
    stored = []
    for msg in extract_messages(payload):
        con.execute(
            """
            INSERT OR IGNORE INTO messages
              (id, wa_id, direction, message_type, text, raw_json, meta_message_id, environment, created_at)
            VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?)
            """,
            (
                msg["id"],
                msg["wa_id"],
                msg["message_type"],
                msg["text"],
                json.dumps(msg["raw"], ensure_ascii=False),
                msg["id"],
                ENVIRONMENT,
                now,
            ),
        )
        con.execute(
            """
            INSERT INTO contacts(wa_id, profile_name, last_inbound_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wa_id) DO UPDATE SET
              profile_name = COALESCE(NULLIF(excluded.profile_name, ''), contacts.profile_name),
              last_inbound_at = excluded.last_inbound_at,
              updated_at = excluded.updated_at
            """,
            (msg["wa_id"], msg["profile_name"], now, now),
        )
        stored.append({"id": msg["id"], "wa_id": msg["wa_id"], "type": msg["message_type"]})
    con.commit()
    con.close()
    return stored


def meta_send(payload):
    if not PHONE_NUMBER_ID:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not set")
    if not ACCESS_TOKEN:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(safe_error(exc.reason)) from exc


def idempotency_key_for(handler, payload):
    key = handler.headers.get("Idempotency-Key", "").strip()
    if key:
        return key
    material = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def store_outbound_attempt(key, to, payload):
    now = utc_now()
    con = db()
    existing = con.execute(
        "SELECT * FROM outbound_attempts WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing:
        con.close()
        return dict(existing), False
    con.execute(
        """
        INSERT INTO outbound_attempts
          (idempotency_key, wa_id, payload_json, status, environment, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (key, to, json.dumps(payload, ensure_ascii=False), ENVIRONMENT, now, now),
    )
    con.commit()
    con.close()
    return None, True


def finish_outbound_attempt(key, to, message_type, label, payload, response=None, error=""):
    now = utc_now()
    error = safe_error(error) if error else ""
    status = "sent" if not error else "error"
    response_json = json.dumps(response or {}, ensure_ascii=False)
    con = db()
    con.execute(
        """
        UPDATE outbound_attempts
        SET status = ?, response_json = ?, error = ?, updated_at = ?
        WHERE idempotency_key = ?
        """,
        (status, response_json, error, now, key),
    )
    if not error:
        meta_id = ""
        messages = (response or {}).get("messages") if isinstance(response, dict) else None
        if messages:
            meta_id = str((messages[0] or {}).get("id", ""))
        local_id = meta_id or f"out:{key}"
        con.execute(
            """
            INSERT OR REPLACE INTO messages
              (id, wa_id, direction, message_type, text, raw_json, meta_message_id, environment, created_at)
            VALUES (?, ?, 'outbound', ?, ?, ?, ?, ?, ?)
            """,
            (
                local_id,
                to,
                message_type,
                label,
                json.dumps(payload, ensure_ascii=False),
                meta_id,
                ENVIRONMENT,
                now,
            ),
        )
        con.execute(
            """
            INSERT INTO contacts(wa_id, last_outbound_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(wa_id) DO UPDATE SET
              last_outbound_at = excluded.last_outbound_at,
              updated_at = excluded.updated_at
            """,
            (to, now, now),
        )
    con.commit()
    con.close()


def build_text_payload(body):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise ValueError("to and text are required")
    return to, "text", text, {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": bool(body.get("preview_url", True)), "body": text},
    }


def build_media_payload(body, media_type):
    to = body.get("to", "")
    link = body.get("link", "")
    if not to or not link:
        raise ValueError("to and link are required")
    media = {"link": link}
    if body.get("caption"):
        media["caption"] = body["caption"]
    if media_type == "document" and body.get("filename"):
        media["filename"] = body["filename"]
    return to, media_type, f"{media_type}:{link}", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": media_type,
        media_type: media,
    }


def build_template_payload(body):
    to = body.get("to", "")
    name = body.get("template_name", "")
    language = body.get("language", "en_US")
    if not to or not name:
        raise ValueError("to and template_name are required")
    template = {"name": name, "language": {"code": language}}
    if body.get("components"):
        template["components"] = body["components"]
    return to, "template", f"template:{name}:{language}", {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }


def build_payload_from_item(item):
    kind = item.get("type", "")
    if kind == "text":
        return build_text_payload(item)
    if kind == "image":
        return build_media_payload(item, "image")
    if kind == "video":
        return build_media_payload(item, "video")
    if kind == "document":
        return build_media_payload(item, "document")
    if kind == "template":
        return build_template_payload(item)
    raise ValueError("package item type must be text, image, video, document, or template")


def send_payload_with_idempotency(handler, payload, to, message_type, label, key):
    existing, created = store_outbound_attempt(key, to, payload)
    if not created:
        if existing.get("error"):
            existing["error"] = safe_error(existing["error"])
        return {"ok": True, "idempotent": True, "attempt": existing}
    try:
        response = meta_send(payload)
        finish_outbound_attempt(key, to, message_type, label, payload, response=response)
        return {"ok": True, "idempotency_key": key, "meta": response}
    except Exception as exc:
        error_text = safe_error(exc)
        finish_outbound_attempt(key, to, message_type, label, payload, error=error_text)
        return {"ok": False, "idempotency_key": key, "error": error_text}


def send_package(handler, package_kind, body):
    to = body.get("to", "")
    items = body.get("items") or []
    if not to:
        raise ValueError("to is required")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")

    base_key = handler.headers.get("Idempotency-Key", "").strip()
    if not base_key:
        material = json.dumps({"package_kind": package_kind, "to": to, "items": items}, sort_keys=True, ensure_ascii=False)
        base_key = hashlib.sha256(material.encode("utf-8")).hexdigest()

    results = []
    for index, original_item in enumerate(items, start=1):
        item = dict(original_item)
        item["to"] = to
        item_key = f"{base_key}:{index}"
        item_to, message_type, label, payload = build_payload_from_item(item)
        result = send_payload_with_idempotency(handler, payload, item_to, message_type, label, item_key)
        result["index"] = index
        result["type"] = item.get("type")
        results.append(result)
        if not result.get("ok"):
            return {"ok": False, "package_kind": package_kind, "idempotency_key": base_key, "results": results}
    return {"ok": True, "package_kind": package_kind, "idempotency_key": base_key, "results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "CanopyWhatsAppTransport/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            return serve_static_asset(self, parsed.path, head_only=True)
        if parsed.path in {"/", "/health"}:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path.startswith("/assets/"):
            return serve_static_asset(self, parsed.path)
        if parsed.path in {"/", "/health"}:
            return json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "canopy-whatsapp-transport",
                    "environment": ENVIRONMENT,
                    "has_phone_number_id": bool(PHONE_NUMBER_ID),
                    "has_access_token": bool(ACCESS_TOKEN),
                    "updated_at": utc_now(),
                },
            )
        if parsed.path == "/webhook/meta":
            mode = qs.get("hub.mode", [""])[0]
            token = qs.get("hub.verify_token", [""])[0]
            challenge = qs.get("hub.challenge", [""])[0]
            if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
                body = challenge.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return json_response(self, 403, {"ok": False, "error": "webhook verification failed"})
        if parsed.path == "/operator-feed":
            limit = int(qs.get("limit", ["20"])[0])
            con = db()
            rows = con.execute(
                """
                SELECT c.wa_id, c.profile_name, c.last_inbound_at, c.last_outbound_at,
                       m.id AS latest_message_id, m.direction, m.message_type, m.text, m.created_at
                FROM contacts c
                LEFT JOIN messages m ON m.id = (
                  SELECT id FROM messages
                  WHERE wa_id = c.wa_id
                  ORDER BY created_at DESC
                  LIMIT 1
                )
                ORDER BY COALESCE(c.last_inbound_at, c.updated_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            con.close()
            return json_response(self, 200, {"ok": True, "contacts": [dict(r) for r in rows]})
        if parsed.path == "/messages":
            wa_id = qs.get("wa_id", [""])[0]
            if not wa_id:
                return json_response(self, 400, {"ok": False, "error": "wa_id is required"})
            con = db()
            rows = con.execute(
                """
                SELECT id, wa_id, direction, message_type, text, meta_message_id, environment, created_at
                FROM messages
                WHERE wa_id = ?
                ORDER BY created_at ASC
                """,
                (wa_id,),
            ).fetchall()
            con.close()
            return json_response(self, 200, {"ok": True, "messages": [dict(r) for r in rows]})
        if parsed.path == "/statuses":
            wa_id = qs.get("wa_id", [""])[0]
            meta_message_id = qs.get("message_id", [""])[0]
            if not wa_id and not meta_message_id:
                return json_response(self, 400, {"ok": False, "error": "wa_id or message_id is required"})
            con = db()
            if meta_message_id:
                rows = con.execute(
                    """
                    SELECT meta_message_id, wa_id, status, status_timestamp, raw_json, environment, created_at
                    FROM message_statuses
                    WHERE meta_message_id = ?
                    ORDER BY created_at ASC
                    """,
                    (meta_message_id,),
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT meta_message_id, wa_id, status, status_timestamp, raw_json, environment, created_at
                    FROM message_statuses
                    WHERE wa_id = ?
                    ORDER BY created_at ASC
                    """,
                    (wa_id,),
                ).fetchall()
            con.close()
            return json_response(self, 200, {"ok": True, "statuses": [dict(r) for r in rows]})
        return json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/webhook/meta":
            try:
                payload = read_json(self)
                stored_messages = store_inbound_payload(payload)
                stored_statuses = store_status_payload(payload)
                return json_response(
                    self,
                    200,
                    {"ok": True, "stored": stored_messages, "stored_statuses": stored_statuses},
                )
            except Exception as exc:
                return json_response(self, 500, {"ok": False, "error": str(exc)})

        ok, error = require_internal_auth(self)
        if not ok:
            code, body = error
            return json_response(self, code, body)

        try:
            body = read_json(self)
            if parsed.path in {"/send/package/agent", "/send/package/client"}:
                package_kind = parsed.path.rsplit("/", 1)[-1]
                result = send_package(self, package_kind, body)
                return json_response(self, 200 if result.get("ok") else 502, result)
            if parsed.path == "/send/text":
                to, message_type, label, payload = build_text_payload(body)
            elif parsed.path == "/send/media":
                media_type = body.get("media_type", "image")
                if media_type not in {"image", "video"}:
                    raise ValueError("media_type must be image or video")
                to, message_type, label, payload = build_media_payload(body, media_type)
            elif parsed.path == "/send/document":
                to, message_type, label, payload = build_media_payload(body, "document")
            elif parsed.path == "/send/template":
                to, message_type, label, payload = build_template_payload(body)
            else:
                return json_response(self, 404, {"ok": False, "error": "not found"})

            key = idempotency_key_for(self, payload)
            result = send_payload_with_idempotency(self, payload, to, message_type, label, key)
            return json_response(self, 200 if result.get("ok") else 502, result)
        except Exception as exc:
            return json_response(self, 400, {"ok": False, "error": str(exc)})


def main():
    db().close()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Canopy WhatsApp transport listening on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
