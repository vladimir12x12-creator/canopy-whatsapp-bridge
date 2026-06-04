#!/usr/bin/env python3
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = os.environ.get(
    "WHATSAPP_BRIDGE_DB",
    "/Users/vm/Documents/Codex/2026-05-27/new-chat/whatsapp_bridge/leads.sqlite",
)
HOST = os.environ.get("WHATSAPP_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHATSAPP_BRIDGE_PORT", "8088"))
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")


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
            segment TEXT,
            priority TEXT,
            last_message_at TEXT,
            escalation_required INTEGER DEFAULT 0,
            next_action TEXT,
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
            message_type TEXT,
            text TEXT,
            raw_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """
    )
    return con


def classify(text):
    t = (text or "").lower()
    if any(x in t for x in ["investor", "investment", "fund", "jv", "roi", "proof of funds", "инвест"]):
        return {
            "segment": "investor",
            "priority": "P1",
            "escalation_required": 1,
            "next_action": "Prepare investor response; escalate if they request call or terms.",
        }
    if any(x in t for x in ["ready", "completed", "finish", "move in", "готов", "когда будет", "срок"]):
        return {
            "segment": "ready_villa_buyer",
            "priority": "P1",
            "escalation_required": 0,
            "next_action": "Send C9 readiness / private viewing response.",
        }
    if any(x in t for x in ["agent", "broker", "agency", "commission", "register client", "агент", "комисс"]):
        return {
            "segment": "broker",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send broker pack: availability, 6% commission, client registration.",
        }
    if any(x in t for x in ["bisp", "british", "school", "kids", "children", "family", "школ", "дет"]):
        return {
            "segment": "family_bisp_buyer",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send family/BISP positioning and offer private C9 viewing.",
        }
    if any(x in t for x in ["price", "payment", "schedule", "цена", "прайс", "платеж"]):
        return {
            "segment": "price_payment",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send current availability and ask which villa/payment scenario they need.",
        }
    return {
        "segment": "new_inbound",
        "priority": "P3",
        "escalation_required": 0,
        "next_action": "Qualify: ask whether they are buying for themselves or representing a client.",
    }


def extract_messages(payload):
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c.get("wa_id"): c for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                wa_id = msg.get("from", "")
                profile_name = (contacts.get(wa_id) or {}).get("profile", {}).get("name", "")
                text = ""
                if msg.get("type") == "text":
                    text = msg.get("text", {}).get("body", "")
                elif msg.get("type"):
                    text = f"[{msg.get('type')} message]"
                out.append(
                    {
                        "message_id": msg.get("id") or f"{wa_id}:{msg.get('timestamp', utc_now())}",
                        "wa_id": wa_id,
                        "profile_name": profile_name,
                        "message_type": msg.get("type", ""),
                        "text": text,
                        "raw": msg,
                    }
                )
    return out


def store_payload(payload):
    con = db()
    now = utc_now()
    con.execute(
        "INSERT INTO webhook_events(raw_json, received_at) VALUES (?, ?)",
        (json.dumps(payload, ensure_ascii=False), now),
    )
    for item in extract_messages(payload):
        classification = classify(item["text"])
        con.execute(
            """
            INSERT OR IGNORE INTO messages
              (id, wa_id, direction, message_type, text, raw_json, received_at)
            VALUES (?, ?, 'inbound', ?, ?, ?, ?)
            """,
            (
                item["message_id"],
                item["wa_id"],
                item["message_type"],
                item["text"],
                json.dumps(item["raw"], ensure_ascii=False),
                now,
            ),
        )
        con.execute(
            """
            INSERT INTO contacts
              (wa_id, profile_name, segment, priority, last_message_at,
               escalation_required, next_action, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wa_id) DO UPDATE SET
              profile_name = COALESCE(NULLIF(excluded.profile_name, ''), contacts.profile_name),
              segment = excluded.segment,
              priority = excluded.priority,
              last_message_at = excluded.last_message_at,
              escalation_required = excluded.escalation_required,
              next_action = excluded.next_action,
              updated_at = excluded.updated_at
            """,
            (
                item["wa_id"],
                item["profile_name"],
                classification["segment"],
                classification["priority"],
                now,
                classification["escalation_required"],
                classification["next_action"],
                now,
            ),
        )
    con.commit()
    con.close()


def rows_to_json(rows):
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/webhook":
            mode = params.get("hub.mode", [""])[0]
            token = params.get("hub.verify_token", [""])[0]
            challenge = params.get("hub.challenge", [""])[0]
            if mode == "subscribe" and token and token == VERIFY_TOKEN:
                body = challenge.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(403, {"error": "verification failed"})
            return
        if parsed.path == "/leads":
            con = db()
            rows = con.execute(
                "SELECT * FROM contacts ORDER BY priority ASC, last_message_at DESC"
            ).fetchall()
            con.close()
            body = rows_to_json(rows)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/messages":
            wa_id = params.get("wa_id", [""])[0]
            con = db()
            rows = con.execute(
                "SELECT * FROM messages WHERE wa_id = ? ORDER BY received_at ASC",
                (wa_id,),
            ).fetchall()
            con.close()
            body = rows_to_json(rows)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/webhook":
            self.send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid json"})
            return
        store_payload(payload)
        self.send_json(200, {"ok": True})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    if not VERIFY_TOKEN:
        print("WARNING: VERIFY_TOKEN is empty. Set VERIFY_TOKEN before using Meta webhooks.")
    print(f"Listening on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
