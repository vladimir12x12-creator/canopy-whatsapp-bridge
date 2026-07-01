#!/usr/bin/env python3
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


HOST = os.environ.get("CANOPY_TRANSPORT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CANOPY_TRANSPORT_PORT", "8091"))
DB_PATH = os.environ.get("CANOPY_TRANSPORT_DB", "./canopy_whatsapp_transport.sqlite")
ENVIRONMENT = os.environ.get("CANOPY_ENV", "staging")
GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WEBHOOK_VERIFY_TOKEN = os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
INTERNAL_TOKEN = os.environ.get("CANOPY_TRANSPORT_INTERNAL_TOKEN", "")


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
    return con


def json_response(handler, code, data):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


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
        raise RuntimeError(str(exc.reason)) from exc


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


class Handler(BaseHTTPRequestHandler):
    server_version = "CanopyWhatsAppTransport/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/health":
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
        return json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/webhook/meta":
            try:
                payload = read_json(self)
                stored = store_inbound_payload(payload)
                return json_response(self, 200, {"ok": True, "stored": stored})
            except Exception as exc:
                return json_response(self, 500, {"ok": False, "error": str(exc)})

        ok, error = require_internal_auth(self)
        if not ok:
            code, body = error
            return json_response(self, code, body)

        try:
            body = read_json(self)
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
            existing, created = store_outbound_attempt(key, to, payload)
            if not created:
                return json_response(self, 200, {"ok": True, "idempotent": True, "attempt": existing})
            try:
                response = meta_send(payload)
                finish_outbound_attempt(key, to, message_type, label, payload, response=response)
                return json_response(self, 200, {"ok": True, "idempotency_key": key, "meta": response})
            except Exception as exc:
                finish_outbound_attempt(key, to, message_type, label, payload, error=str(exc))
                return json_response(self, 502, {"ok": False, "idempotency_key": key, "error": str(exc)})
        except Exception as exc:
            return json_response(self, 400, {"ok": False, "error": str(exc)})


def main():
    db().close()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Canopy WhatsApp transport listening on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
