#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = os.environ.get("BRIDGE_PUBLIC_BASE_URL", "https://canopy-whatsapp-bridge.onrender.com").rstrip("/")
SEND_TOKEN = os.environ.get("BRIDGE_SEND_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
MODEL = os.environ.get("CODEX_RELAY_MODEL", "gpt-4.1").strip()
POLL_SECONDS = max(5, int(os.environ.get("CODEX_RELAY_POLL_SECONDS", "15")))
FEED_LIMIT = max(1, min(50, int(os.environ.get("CODEX_RELAY_FEED_LIMIT", "20"))))
DRY_RUN = os.environ.get("CODEX_RELAY_DRY_RUN", "0").strip().lower() in {"1", "true", "yes", "on"}
PROCESS_EXISTING = os.environ.get("CODEX_RELAY_PROCESS_EXISTING", "0").strip().lower() in {"1", "true", "yes", "on"}
STATE_PATH = Path(
    os.environ.get(
        "CODEX_RELAY_STATE_PATH",
        "/var/data/codex_relay_state.json" if Path("/var/data").exists() else ".codex_relay_state.json",
    )
)
KNOWLEDGE_PATH = Path(__file__).with_name("codex_relay_knowledge.md")
TEST_START_COMMANDS = {"тест", "test"}
TEST_STOP_COMMANDS = {
    "тест закончен",
    "тест окончен",
    "закончили тест",
    "стоп тест",
    "stop test",
    "test finished",
    "end test",
    "рабочий режим",
    "work mode",
    "обычный режим",
}


def log(message):
    print(f"[codex-relay] {message}", flush=True)


def request_json(method, url, payload=None, headers=None, timeout=45):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc


def load_state():
    if not STATE_PATH.exists():
        return {"seen": {}, "created_at": time.time()}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen": {}, "created_at": time.time(), "state_error": "invalid json was reset"}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def mark_seen(state, message_id, status, reply="", error=""):
    seen = state.setdefault("seen", {})
    seen[message_id] = {
        "status": status,
        "reply": reply[:1000],
        "error": error[:1000],
        "updated_at": time.time(),
    }
    if len(seen) > 500:
        for key in sorted(seen, key=lambda k: seen[k].get("updated_at", 0))[:-400]:
            seen.pop(key, None)
    save_state(state)


def fetch_feed():
    return request_json("GET", f"{BASE_URL}/operator-feed?limit={FEED_LIMIT}", timeout=30)


def fetch_recent_messages(wa_id, limit=12):
    query = urllib.parse.urlencode({"wa_id": wa_id})
    rows = request_json("GET", f"{BASE_URL}/messages?{query}", timeout=30)
    if not isinstance(rows, list):
        return []
    return rows[-limit:]


def send_text(to, text):
    if DRY_RUN:
        log(f"dry-run reply to {to}: {text}")
        return {"ok": True, "dry_run": True}
    if not SEND_TOKEN:
        raise RuntimeError("BRIDGE_SEND_TOKEN is not set")
    return request_json(
        "POST",
        f"{BASE_URL}/send-text",
        {"to": to, "text": text},
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Token": SEND_TOKEN,
        },
        timeout=45,
    )


def normalize_command(text):
    normalized = " ".join((text or "").strip().lower().split())
    return normalized.strip(" .,!?:;\"'«»()[]{}")


def operator_control_reply(item):
    if not item.get("is_operator"):
        return ""
    command = normalize_command(item.get("text"))
    if command in TEST_START_COMMANDS:
        return (
            "Тестовый режим включён. Следующие сообщения воспринимаю как симуляцию входящего лида/агента. "
            "Чтобы выйти: «тест закончен», «стоп тест» или «рабочий режим»."
        )
    if command in TEST_STOP_COMMANDS:
        return "Тестовый режим выключен. Дальше WhatsApp снова работает как обычный канал диалога с Codex по рабочим задачам."
    return ""


def build_prompt(item, recent_messages, knowledge):
    history = []
    for row in recent_messages:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        history.append(
            {
                "direction": row.get("direction", ""),
                "message_type": row.get("message_type", ""),
                "text": text,
                "received_at": row.get("received_at", ""),
            }
        )
    return [
        {
            "role": "system",
            "content": (
                "You are the Canopy Hills Codex-side WhatsApp relay. "
                "You are not a rigid bot and not a template sender. "
                "Write one concise WhatsApp reply that directly answers the latest message. "
                "Use the same language as the contact unless the context clearly requires otherwise. "
                "No markdown headings. No JSON. No internal analysis. "
                "Do not invent facts, prices, payment plans, dates, legal advice, discounts, or ROI. "
                "If escalation is needed, say you will check/prepare it with the team. "
                "If the sender is Vladimir/operator outside test mode, answer as an internal teammate."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "canonical_knowledge": knowledge,
                    "latest_operator_feed_item": item,
                    "recent_message_history": history,
                    "output_requirement": "Return only the WhatsApp reply text to send now.",
                },
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]


def openai_reply(item, recent_messages, knowledge):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": MODEL,
        "messages": build_prompt(item, recent_messages, knowledge),
        "temperature": 0.35,
        "max_tokens": 450,
    }
    data = request_json(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI returned no choices: {data}")
    reply = (choices[0].get("message") or {}).get("content", "").strip()
    if not reply:
        raise RuntimeError(f"OpenAI returned an empty reply: {data}")
    return reply


def should_skip_item(item):
    text = (item.get("text") or "").strip()
    if not text:
        return "empty text"
    if item.get("message_type") not in {"text", "audio", "button", "interactive"}:
        return f"unsupported message type {item.get('message_type')}"
    return ""


def process_once(state, knowledge):
    feed = fetch_feed()
    if not isinstance(feed, list):
        raise RuntimeError(f"operator-feed returned non-list: {feed}")
    if not PROCESS_EXISTING and not state.get("initialized"):
        for item in feed:
            message_id = item.get("message_id")
            if message_id:
                state.setdefault("seen", {})[message_id] = {
                    "status": "initial_skip",
                    "updated_at": time.time(),
                }
        state["initialized"] = True
        save_state(state)
        log(f"initialized; skipped {len(feed)} existing feed messages")
        return 0

    processed = 0
    for item in reversed(feed):
        message_id = item.get("message_id")
        wa_id = item.get("wa_id")
        if not message_id or not wa_id:
            continue
        seen_item = state.get("seen", {}).get(message_id)
        if seen_item:
            status = seen_item.get("status")
            updated_at = seen_item.get("updated_at", 0)
            if status in {"sent", "initial_skip", "skipped"}:
                continue
            if status == "error" and time.time() - updated_at < 90:
                continue
        reason = should_skip_item(item)
        if reason:
            mark_seen(state, message_id, "skipped", error=reason)
            continue
        try:
            reply = operator_control_reply(item)
            if not reply:
                recent = fetch_recent_messages(wa_id)
                reply = openai_reply(item, recent, knowledge)
            send_text(wa_id, reply)
            mark_seen(state, message_id, "sent", reply=reply)
            log(f"sent reply to {wa_id} for {message_id}")
            processed += 1
        except Exception as exc:
            mark_seen(state, message_id, "error", error=str(exc))
            log(f"error for {message_id}: {exc}")
    return processed


def main():
    if not KNOWLEDGE_PATH.exists():
        raise SystemExit(f"knowledge file not found: {KNOWLEDGE_PATH}")
    knowledge = KNOWLEDGE_PATH.read_text(encoding="utf-8")
    state = load_state()
    log(
        "started "
        f"base={BASE_URL} model={MODEL} poll={POLL_SECONDS}s dry_run={DRY_RUN} "
        f"state={STATE_PATH}"
    )
    while True:
        try:
            process_once(state, knowledge)
            state = load_state()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log(f"loop error: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
