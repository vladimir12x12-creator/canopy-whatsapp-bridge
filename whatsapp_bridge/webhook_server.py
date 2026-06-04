#!/usr/bin/env python3
import json
import os
import sqlite3
from html import escape
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


def draft_reply(contact):
    segment = contact.get("segment") or "new_inbound"
    if segment == "investor":
        return (
            "Hi, thank you for your interest in Canopy Hills. "
            "We can share the current construction status, available investment scenarios, "
            "and arrange a call if you would like to discuss terms in detail."
        )
    if segment == "ready_villa_buyer":
        return (
            "Hi, thank you for reaching out. Our first villa is moving toward show-ready status, "
            "and we can arrange a private preview. May I ask if you are looking for a home for your family "
            "or reviewing options for a client?"
        )
    if segment == "broker":
        return (
            "Hi, thank you for contacting Canopy Hills. We work with agents on a 6% commission basis. "
            "Please let us know whether you would like the latest availability, price list, and client registration details."
        )
    if segment == "family_bisp_buyer":
        return (
            "Hi, thank you for your interest. Canopy Hills is designed for long-term family living near BISP, "
            "with spacious villas, views, privacy, and everyday infrastructure nearby. "
            "Would you like to arrange a private viewing?"
        )
    if segment == "price_payment":
        return (
            "Hi, thank you for your message. I can share the current availability and payment options. "
            "Are you considering a villa for yourself, or are you representing a client?"
        )
    return (
        "Hi, thank you for contacting Canopy Hills. May I ask if you are looking for a villa for yourself, "
        "or are you representing a client?"
    )


def page(title, body):
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f5f1;
      --paper: #ffffff;
      --ink: #1c1f1c;
      --muted: #687068;
      --line: #dedbd2;
      --accent: #2f5d50;
      --warn: #8a5b00;
      --hot: #9e2f2f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 22px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--paper);
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    h1 {{ margin: 0; font-size: 22px; font-weight: 650; }}
    main {{ padding: 22px 28px 40px; max-width: 1180px; margin: 0 auto; }}
    a {{ color: var(--accent); text-decoration: none; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--paper); border: 1px solid var(--line); }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    tr:last-child td {{ border-bottom: 0; }}
    .toolbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 14px; }}
    .muted {{ color: var(--muted); }}
    .pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; background: #faf9f5; }}
    .p1 {{ color: var(--hot); font-weight: 650; }}
    .p2 {{ color: var(--warn); font-weight: 650; }}
    .p3 {{ color: var(--muted); font-weight: 650; }}
    .panel {{ background: var(--paper); border: 1px solid var(--line); padding: 16px; margin-bottom: 18px; }}
    .msg {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
    .msg:last-child {{ border-bottom: 0; }}
    pre {{ white-space: pre-wrap; margin: 0; font: inherit; }}
    .draft {{ border-left: 4px solid var(--accent); }}
  </style>
</head>
<body>
  <header><h1>{escape(title)}</h1></header>
  <main>{body}</main>
</body>
</html>"""
    return html.encode("utf-8")


def render_inbox():
    con = db()
    rows = con.execute(
        """
        SELECT c.*,
               (
                 SELECT text FROM messages m
                 WHERE m.wa_id = c.wa_id
                 ORDER BY m.received_at DESC
                 LIMIT 1
               ) AS last_text
        FROM contacts c
        ORDER BY c.priority ASC, c.last_message_at DESC
        """
    ).fetchall()
    con.close()
    body = [
        '<div class="toolbar">',
        '<div class="muted">Staging WhatsApp inbox. Refresh the page after sending a test message.</div>',
        '<div><a href="/leads">JSON leads</a> · <a href="/events">Webhook events</a></div>',
        "</div>",
        "<table>",
        "<thead><tr><th>Lead</th><th>Segment</th><th>Priority</th><th>Last message</th><th>Next action</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        contact = dict(row)
        priority = escape(contact.get("priority") or "")
        body.append(
            "<tr>"
            f"<td><a href=\"/lead?wa_id={escape(contact['wa_id'])}\">{escape(contact.get('profile_name') or contact['wa_id'])}</a>"
            f"<div class=\"muted\">{escape(contact['wa_id'])}</div></td>"
            f"<td><span class=\"pill\">{escape(contact.get('segment') or '')}</span></td>"
            f"<td><span class=\"{priority.lower()}\">{priority}</span></td>"
            f"<td>{escape(contact.get('last_text') or '')}<div class=\"muted\">{escape(contact.get('last_message_at') or '')}</div></td>"
            f"<td>{escape(contact.get('next_action') or '')}</td>"
            "</tr>"
        )
    if not rows:
        body.append('<tr><td colspan="5" class="muted">No leads yet.</td></tr>')
    body.extend(["</tbody>", "</table>"])
    return page("Canopy WhatsApp Inbox", "".join(body))


def render_lead(wa_id):
    con = db()
    contact = con.execute("SELECT * FROM contacts WHERE wa_id = ?", (wa_id,)).fetchone()
    messages = con.execute(
        "SELECT * FROM messages WHERE wa_id = ? ORDER BY received_at ASC",
        (wa_id,),
    ).fetchall()
    con.close()
    if not contact:
        return page("Lead not found", '<p><a href="/inbox">Back to inbox</a></p><p>Lead not found.</p>')
    contact_dict = dict(contact)
    chunks = [
        '<p><a href="/inbox">Back to inbox</a></p>',
        '<section class="panel">',
        f"<div><strong>{escape(contact_dict.get('profile_name') or wa_id)}</strong> <span class=\"muted\">{escape(wa_id)}</span></div>",
        f"<div>Segment: <span class=\"pill\">{escape(contact_dict.get('segment') or '')}</span> ",
        f"Priority: <span class=\"{escape((contact_dict.get('priority') or '').lower())}\">{escape(contact_dict.get('priority') or '')}</span></div>",
        f"<div class=\"muted\">Next action: {escape(contact_dict.get('next_action') or '')}</div>",
        "</section>",
        '<section class="panel draft">',
        "<h2>Suggested reply</h2>",
        f"<pre>{escape(draft_reply(contact_dict))}</pre>",
        "</section>",
        '<section class="panel">',
        "<h2>Messages</h2>",
    ]
    for message in messages:
        item = dict(message)
        chunks.append(
            '<div class="msg">'
            f"<div><strong>{escape(item.get('direction') or '')}</strong> <span class=\"muted\">{escape(item.get('received_at') or '')}</span></div>"
            f"<pre>{escape(item.get('text') or '')}</pre>"
            "</div>"
        )
    chunks.append("</section>")
    return page(f"Lead: {contact_dict.get('profile_name') or wa_id}", "".join(chunks))


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
        if parsed.path == "/" or parsed.path == "/inbox":
            body = render_inbox()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/lead":
            body = render_lead(params.get("wa_id", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        if parsed.path == "/events":
            con = db()
            rows = con.execute(
                "SELECT id, raw_json, received_at FROM webhook_events ORDER BY id DESC LIMIT 20"
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
