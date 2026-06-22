#!/usr/bin/env python3
import json
import mimetypes
import os
import sqlite3
import csv
import io
import uuid
import urllib.error
import urllib.request
from html import escape
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

DB_PATH = os.environ.get(
    "WHATSAPP_BRIDGE_DB",
    "/Users/vm/Documents/Codex/2026-05-27/new-chat/whatsapp_bridge/leads.sqlite",
)
HOST = os.environ.get("WHATSAPP_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHATSAPP_BRIDGE_PORT", "8088"))
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")
SEND_API_TOKEN = os.environ.get("BRIDGE_SEND_TOKEN", "")
DEFAULT_WABA_ID = os.environ.get("WHATSAPP_WABA_ID", "2253327871868025")
DEFAULT_APP_ID = os.environ.get("WHATSAPP_APP_ID", "1693287358483119")
BASE_URL = os.environ.get("BRIDGE_PUBLIC_BASE_URL", "https://canopy-whatsapp-bridge.onrender.com").rstrip("/")
ASSET_DIR = Path(__file__).resolve().parent / "assets"
CANOPY_MARKET_INTEL_BATCH_MARKER = Path(DB_PATH).with_name("canopy_market_intel_batch_20260617.sent.json")
DEVELOPER_RESEARCH_TARGETS = [
    {
        "developer": "Botanica Luxury Villas",
        "project": "Botanica villa collections",
        "area": "Phuket",
        "wa_id": "66983947097",
        "contact_source": "Official website contact page",
        "channel": "WhatsApp",
    },
    {
        "developer": "Anchan Villas",
        "project": "Anchan Villas",
        "area": "Phuket",
        "wa_id": "66923899000",
        "contact_source": "Official website/social public contact",
        "channel": "WhatsApp",
    },
    {
        "developer": "Trichada Villas",
        "project": "Trichada Villas",
        "area": "Bang Tao / Layan",
        "wa_id": "66945933980",
        "contact_source": "Official contact page",
        "channel": "WhatsApp",
    },
    {
        "developer": "Andaman Asset Solution",
        "project": "The Trinity Village",
        "area": "Phuket",
        "wa_id": "66618190731",
        "contact_source": "Official website public sales contact",
        "channel": "WhatsApp",
    },
    {
        "developer": "Mouana Phuket",
        "project": "Mouana villa products",
        "area": "Phuket",
        "wa_id": "66801468234",
        "contact_source": "Public website phone/contact",
        "channel": "WhatsApp",
    },
]
DEVELOPER_RESEARCH_WA_IDS = {item["wa_id"] for item in DEVELOPER_RESEARCH_TARGETS}
ENABLE_TEST_AUTOREPLY = os.environ.get("ENABLE_TEST_AUTOREPLY", "0").strip().lower() in {"1", "true", "yes", "on"}
TEST_AUTOREPLY_WA_IDS = {
    x.strip() for x in os.environ.get("TEST_AUTOREPLY_WA_IDS", "66628512432").split(",") if x.strip()
}
TEST_AUTOREPLY_REQUIRE_EXPLICIT_PREFIX = (
    os.environ.get("TEST_AUTOREPLY_REQUIRE_EXPLICIT_PREFIX", "1").strip().lower()
    in {"1", "true", "yes", "on"}
)
TEST_AUTOREPLY_PREFIXES = (
    "тест",
    "test",
    "клиент:",
    "агент:",
    "покупатель:",
    "инвестор:",
    "lead:",
    "client:",
    "agent:",
    "buyer:",
    "investor:",
)
ENABLE_AI_AGENT = os.environ.get("ENABLE_AI_AGENT", "1").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_BRIDGE_AUTONOMOUS_REPLIES = (
    os.environ.get("ENABLE_BRIDGE_AUTONOMOUS_REPLIES", "0").strip().lower()
    in {"1", "true", "yes", "on"}
)
ENABLE_AI_AUDIO_TRANSCRIPTION = (
    os.environ.get("ENABLE_AI_AUDIO_TRANSCRIPTION", "1").strip().lower()
    in {"1", "true", "yes", "on"}
)
ENABLE_AI_AGENT_TOOLS = os.environ.get("ENABLE_AI_AGENT_TOOLS", "0").strip().lower() in {"1", "true", "yes", "on"}
AGENT_WELCOME_PACK_APPROVED = (
    os.environ.get("AGENT_WELCOME_PACK_APPROVED", "1").strip().lower()
    in {"1", "true", "yes", "on"}
)
AI_AGENT_DRY_RUN = os.environ.get("AI_AGENT_DRY_RUN", "0").strip().lower() in {"1", "true", "yes", "on"}
AI_AGENT_MODEL = os.environ.get("AI_AGENT_MODEL", "gpt-4.1-mini").strip()
AI_AGENT_MAX_CHARS = int(os.environ.get("AI_AGENT_MAX_CHARS", "1200"))
AI_OPERATOR_WA_IDS = {
    x.strip() for x in os.environ.get("AI_OPERATOR_WA_IDS", "66628512432").split(",") if x.strip()
}
AI_AGENT_WA_ID_ALLOWLIST = {
    x.strip() for x in os.environ.get("AI_AGENT_WA_ID_ALLOWLIST", "").split(",") if x.strip()
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
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
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_modes (
            wa_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_research_targets (
            wa_id TEXT PRIMARY KEY,
            developer TEXT NOT NULL,
            project TEXT,
            area TEXT,
            contact_source TEXT,
            channel TEXT,
            status TEXT NOT NULL DEFAULT 'target',
            first_message TEXT,
            last_outreach_at TEXT,
            last_reply_at TEXT,
            materials_received INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            next_question TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_research_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_id TEXT NOT NULL,
            developer TEXT,
            direction TEXT NOT NULL,
            event_type TEXT NOT NULL,
            text TEXT,
            raw_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    for target in DEVELOPER_RESEARCH_TARGETS:
        con.execute(
            """
            INSERT OR IGNORE INTO developer_research_targets
              (wa_id, developer, project, area, contact_source, channel, status, next_question, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'target', 'Send first broker pack request when outbound is production-ready.', ?)
            """,
            (
                target["wa_id"],
                target["developer"],
                target["project"],
                target["area"],
                target["contact_source"],
                target["channel"],
                utc_now(),
            ),
        )
        con.execute("DELETE FROM contacts WHERE wa_id = ?", (target["wa_id"],))
    con.commit()
    return con


def has_any(text, terms):
    return any(x in text for x in terms)


def classify(text):
    t = (text or "").lower()
    spam_terms = [
        "seo",
        "website development",
        "digital marketing",
        "loan",
        "crypto",
        "job",
        "vacancy",
        "resume",
        "работу",
        "резюме",
        "кредит",
        "реклама сайта",
    ]
    if has_any(t, spam_terms):
        return {
            "segment": "low_relevance",
            "priority": "P4",
            "escalation_required": 0,
            "next_action": "Do not spend sales time unless they clarify a real villa request.",
        }
    if has_any(t, ["register client", "register my client", "client registration", "registration", "зарегистр", "регистрация клиента"]):
        return {
            "segment": "client_registration",
            "priority": "P1",
            "escalation_required": 1,
            "next_action": "Register client, collect name/country/timing, and confirm viewing or next step.",
        }
    if has_any(t, ["investor", "investment", "fund", "jv", "roi", "proof of funds", "buyback", "co-invest", "инвест", "инвести", "доходность"]):
        return {
            "segment": "investor",
            "priority": "P1",
            "escalation_required": 1,
            "next_action": "Escalate to Vladimir/partner; offer a short call and send investor materials only after qualification.",
        }
    if has_any(t, ["agent", "broker", "agency", "agencies", "commission", "realtor", "co-broker", "cooperate", "cooperation", "client registration", "register client", "my client", "for my client", "representing a client", "агент", "брокер", "комисс", "риэлтор", "сотруднич", "регистрация клиента", "зарегистрировать клиента", "мой клиент", "представляю клиента"]):
        return {
            "segment": "broker",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send broker pack: availability, 6% commission, client registration.",
        }
    if has_any(t, ["details", "send materials", "project materials", "sales kit", "salekit", "brochure", "presentation", "deck", "pdf", "send info", "send more", "share with my client", "подроб", "пришлите материалы", "отправьте материалы", "материалы по проекту", "презентац", "брошюр", "информац"]):
        return {
            "segment": "materials_request",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Qualify whether this is a buyer/family, investor, or agent before sending role-specific materials.",
        }
    if has_any(t, ["quality", "engineering", "insulation", "sound", "roof", "windows", "construction quality", "specs", "качество", "инженер", "изоляц", "крыша", "окна", "строительств"]):
        return {
            "segment": "quality_engineering",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Explain quality as daily-living proof and offer engineering/materials pack or technical viewing.",
        }
    if has_any(t, ["contract", "title", "chanote", "permit", "lawyer", "legal", "leasehold", "freehold", "due diligence", "ownership", "договор", "чанот", "юрист", "документ", "лизхолд", "фрихолд", "собствен"]):
        return {
            "segment": "trust_legal",
            "priority": "P2",
            "escalation_required": 1,
            "next_action": "Acknowledge due diligence, qualify villa/client seriousness, and prepare legal pack only after qualification.",
        }
    if has_any(t, ["viewing", "visit", "appointment", "show", "смотреть", "посмотреть", "показ", "встреч", "визит"]):
        return {
            "segment": "viewing_request",
            "priority": "P1",
            "escalation_required": 1,
            "next_action": "Offer two viewing slots and confirm whether they are buyer or agent.",
        }
    if has_any(t, ["ready", "completed", "finish", "completion", "move in", "move-in", "готов", "когда будет", "срок", "заселиться", "переехать"]):
        return {
            "segment": "ready_villa_buyer",
            "priority": "P1",
            "escalation_required": 0,
            "next_action": "Explain C9 readiness, active construction of next villas, and offer private preview.",
        }
    if has_any(t, ["bisp", "british", "school", "kids", "children", "family", "relocation", "long term", "школ", "дет", "семь", "переезд", "жить"]):
        return {
            "segment": "family_bisp_buyer",
            "priority": "P1",
            "escalation_required": 0,
            "next_action": "Position as long-term family living near BISP; ask timing and offer preview.",
        }
    if has_any(t, ["price", "payment", "schedule", "availability", "available", "цена", "прайс", "платеж", "доступ", "свобод"]):
        return {
            "segment": "price_payment",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send availability/price context and ask whether they consider ready villa, under-construction villa, or larger C1-C3.",
        }
    return {
        "segment": "new_inbound",
        "priority": "P3",
        "escalation_required": 0,
        "next_action": "Qualify: ask whether they are buying for themselves or representing a client.",
    }


def is_developer_research_wa_id(wa_id):
    return bool(wa_id and wa_id in DEVELOPER_RESEARCH_WA_IDS)


def developer_research_event_type(item):
    message_type = (item.get("message_type") or "").lower()
    text = (item.get("text") or "").lower()
    if message_type in {"document", "image", "video"}:
        return "material_received"
    if has_any(text, ["brochure", "broker", "agent pack", "price list", "availability", "payment schedule", "commission"]):
        return "material_or_terms_discussed"
    if has_any(text, ["site inspection", "viewing", "visit", "show villa", "appointment"]):
        return "site_inspection_discussed"
    if has_any(text, ["registration", "register client", "client register", "mou", "agreement"]):
        return "client_registration_discussed"
    return "developer_reply"


def developer_research_next_question(last_text, message_type):
    text = (last_text or "").lower()
    if message_type in {"document", "image", "video"} or has_any(text, ["brochure", "price list", "availability"]):
        return "Confirm commission/cooperation terms, client registration period, and what can be shared with end buyers."
    if "commission" in text and not has_any(text, ["registration", "register"]):
        return "Ask for client registration rule and registration protection period."
    if has_any(text, ["registration", "register"]):
        return "Ask whether an MOU/agency agreement is required before sharing full broker materials."
    if has_any(text, ["site inspection", "viewing", "visit", "show villa"]):
        return "Confirm whether a product-learning site inspection is acceptable for Hugs Management agents."
    return "Ask for broker pack, price list, availability, payment schedule, commission, and client registration process."


def update_developer_research_from_inbound(con, item, now):
    target = con.execute(
        "SELECT * FROM developer_research_targets WHERE wa_id = ?",
        (item["wa_id"],),
    ).fetchone()
    if not target:
        return False

    event_type = developer_research_event_type(item)
    material_flag = 1 if event_type in {"material_received", "material_or_terms_discussed"} else 0
    next_question = developer_research_next_question(item.get("text") or "", item.get("message_type") or "")
    con.execute(
        """
        UPDATE developer_research_targets
        SET status = 'replied',
            last_reply_at = ?,
            reply_count = COALESCE(reply_count, 0) + 1,
            materials_received = CASE WHEN ? THEN 1 ELSE COALESCE(materials_received, 0) END,
            next_question = ?,
            updated_at = ?
        WHERE wa_id = ?
        """,
        (now, material_flag, next_question, now, item["wa_id"]),
    )
    con.execute(
        """
        INSERT INTO developer_research_events
          (wa_id, developer, direction, event_type, text, raw_json, created_at)
        VALUES (?, ?, 'inbound', ?, ?, ?, ?)
        """,
        (
            item["wa_id"],
            target["developer"],
            event_type,
            item.get("text") or "",
            json.dumps(item.get("raw") or {}, ensure_ascii=False),
            now,
        ),
    )
    return True


def log_developer_research_outbound(to, text, response, now):
    con = db()
    target = con.execute(
        "SELECT * FROM developer_research_targets WHERE wa_id = ?",
        (to,),
    ).fetchone()
    if not target:
        con.close()
        return
    con.execute(
        """
        UPDATE developer_research_targets
        SET status = 'outreach_sent',
            first_message = COALESCE(first_message, ?),
            last_outreach_at = ?,
            next_question = 'Wait for reply. If no reply after 48h, send one concise follow-up.',
            updated_at = ?
        WHERE wa_id = ?
        """,
        (text, now, now, to),
    )
    con.execute(
        """
        INSERT INTO developer_research_events
          (wa_id, developer, direction, event_type, text, raw_json, created_at)
        VALUES (?, ?, 'outbound', 'outreach_sent', ?, ?, ?)
        """,
        (
            to,
            target["developer"],
            text,
            json.dumps(response, ensure_ascii=False),
            now,
        ),
    )
    con.commit()
    con.close()


def looks_like_sales_roleplay(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    if TEST_AUTOREPLY_REQUIRE_EXPLICIT_PREFIX:
        return t.startswith(TEST_AUTOREPLY_PREFIXES)
    roleplay_terms = [
        "тест",
        "клиент:",
        "агент:",
        "покупатель:",
        "инвестор:",
        "lead:",
        "client:",
        "agent:",
        "buyer:",
        "investor:",
    ]
    sales_terms = [
        "хочу узнать",
        "узнать больше",
        "интересует проект",
        "интересно узнать",
        "пришлите информацию",
        "пришлите материалы",
        "send details",
        "send info",
        "more information",
        "project details",
        "villa",
        "вилла",
        "виллы",
        "bisp",
        "british international",
        "цена",
        "прайс",
        "price",
        "availability",
        "просмотр",
        "viewing",
        "для клиента",
        "для себя",
        "for client",
        "for my client",
        "for myself",
        "representing a client",
        "представляю клиента",
    ]
    return has_any(t, roleplay_terms) or has_any(t, sales_terms)


def generate_test_autoreply(item):
    if not ENABLE_TEST_AUTOREPLY:
        return ""
    if item.get("wa_id") not in TEST_AUTOREPLY_WA_IDS:
        return ""
    if item.get("message_type") != "text":
        return ""
    text = item.get("text") or ""
    if not looks_like_sales_roleplay(text):
        if TEST_AUTOREPLY_REQUIRE_EXPLICIT_PREFIX:
            return ""
        return (
            "Принял. Это похоже на рабочее сообщение, не на тест клиента. "
            "Если хочешь проверить sales-сценарий, напиши в формате: "
            "«ТЕСТ клиент: хочу узнать больше о проекте» или "
            "«ТЕСТ агент: клиент ищет виллу рядом с BISP»."
        )

    t = text.lower()
    if is_agent_materials_scenario(text) or classify(text).get("segment") in {"broker", "materials_request", "client_registration"}:
        return ""
    if has_any(t, ["для клиента", "for client", "for my client", "representing a client", "представляю клиента"]):
        return (
            "Понял, вы смотрите проект для клиента. В агентском сценарии сначала отправляем стандартный пакет: "
            "видео с позиционированием и карусель преимуществ. После этого можно перейти к регистрации клиента "
            "или согласованию просмотра."
        )
    if has_any(t, ["для себя", "for myself", "for my family", "for family", "себе", "для семьи"]):
        return (
            "Понял, для себя/семьи. Canopy Hills рассчитан именно на постоянную семейную жизнь на Пхукете: "
            "BISP рядом, просторные виллы, вид, тихая зеленая локация и большие usable участки. "
            "Вы уже живете на Пхукете или планируете переезд к учебному году?"
        )

    classification = classify(text)
    if classification["segment"] == "price_payment":
        return (
            "Да, отправлю актуальную доступность и цены. Чтобы дать правильную версию, "
            "подскажите, пожалуйста: вы смотрите виллу для себя/семьи или представляете клиента? "
            "И вам важна готовая/почти готовая вилла или можно рассматривать виллы в строительстве?"
        )
    if classification["segment"] == "broker":
        return (
            "Спасибо. Агентский сценарий: сначала отправляем стандартный пакет Canopy Hills "
            "с видео и каруселью преимуществ. Стандартная комиссия для агентов — 6%."
        )
    if classification["segment"] == "family_bisp_buyer":
        return (
            "Canopy Hills как раз рассчитан на долгосрочную семейную жизнь рядом с BISP: "
            "просторные виллы, вид, тихая зеленая локация, usable участки и повседневная "
            "инфраструктура рядом. Вы уже живете на Пхукете или планируете переезд к учебному году?"
        )
    if classification["segment"] == "ready_villa_buyer":
        return (
            "Понимаю, готовность сейчас ключевой вопрос. Первая вилла C9 будет готова к private preview "
            "в начале/середине августа, а следующие виллы уже строятся. Хотите, чтобы я поставил вас "
            "или вашего клиента в priority preview list?"
        )
    if classification["segment"] == "investor":
        return (
            "Спасибо. Инвестиционные разговоры по Canopy Hills мы ведем отдельно от стандартной продажи вилл. "
            "Чтобы понять, есть ли предметный интерес, лучше согласовать короткий звонок с owner/developer side. "
            "Какой формат вы рассматриваете: покупка виллы, инвестиция в конкретную виллу или проектное финансирование?"
        )
    return (
        "Здравствуйте! Спасибо за интерес к Canopy Hills Villas. Это клубный поселок из 9 просторных "
        "видовых семейных вилл на холме напротив British International School Phuket, созданный для "
        "постоянной жизни на Пхукете, а не для краткосрочной аренды.\n\n"
        "Чтобы отправить вам самые релевантные материалы, подскажите, пожалуйста: вы смотрите виллу "
        "для себя/семьи или представляете клиента?"
    )


def should_ai_agent_reply(item, classification):
    if not ENABLE_AI_AGENT:
        return False
    if not ENABLE_BRIDGE_AUTONOMOUS_REPLIES:
        return False
    if is_developer_research_wa_id(item.get("wa_id")):
        return False
    if item.get("message_type") != "text":
        return False
    if not (item.get("text") or "").strip():
        return False
    if AI_AGENT_WA_ID_ALLOWLIST and item.get("wa_id") not in AI_AGENT_WA_ID_ALLOWLIST:
        return False
    if classification.get("segment") == "low_relevance":
        return True
    return True


def normalize_mode_command(text):
    normalized = " ".join((text or "").strip().lower().split())
    return normalized.strip(" .,!?:;\"'«»()[]{}")


def get_operator_mode(con, wa_id):
    row = con.execute("SELECT mode FROM operator_modes WHERE wa_id = ?", (wa_id,)).fetchone()
    return row["mode"] if row else ""


def set_operator_mode(con, wa_id, mode):
    con.execute(
        """
        INSERT INTO operator_modes(wa_id, mode, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(wa_id) DO UPDATE SET
          mode = excluded.mode,
          updated_at = excluded.updated_at
        """,
        (wa_id, mode, utc_now()),
    )


def operator_test_mode_command(con, item):
    if item.get("wa_id") not in AI_OPERATOR_WA_IDS:
        return ""
    if item.get("message_type") != "text":
        return ""
    command = normalize_mode_command(item.get("text"))
    if command in {"тест", "test"}:
        set_operator_mode(con, item["wa_id"], "lead_test")
        return (
            "Тестовый режим включён. Следующие сообщения от тебя буду воспринимать как симуляцию "
            "входящего лида/агента, а не как рабочую переписку. Чтобы выйти: «тест закончен», «стоп тест» или «рабочий режим»."
        )
    if command in {
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
    }:
        set_operator_mode(con, item["wa_id"], "work")
        return "Тестовый режим выключен. Дальше WhatsApp снова работает как обычный канал диалога с Codex по рабочим задачам."
    return ""


def ai_agent_system_prompt(is_operator=False):
    role = (
        "You are Codex/Canopy Hills internal operations assistant speaking with Vladimir in WhatsApp. "
        "If operator_test_mode is true, this is a simulated external lead conversation; do not treat the speaker as Vladimir."
        if is_operator
        else "You are the Canopy Hills Villas WhatsApp sales assistant."
    )
    return f"""{role}

Use the user's language. Keep WhatsApp replies concise, concrete and useful.
Your job is to sell Canopy Hills intelligently, not merely answer messages. Every external reply should move the conversation toward role qualification, correct material delivery, client registration, viewing, call, or escalation.
Tone: warm, good-natured, light and human. A little tasteful humor is welcome when the moment is casual, but do not joke about legal, payment, availability, negotiation, or other serious topics. Never sound like a stiff corporate bot.
Russian external-client/agent tone: use polite "Вы" by default, not "ты". In Russian, describe Canopy Hills as "клубный посёлок", not just "клуб" or "комплекс".
If a simulated lead gives a name, use that name. Never call a test lead Vladimir unless the simulated lead explicitly introduced himself as Vladimir.

Project facts:
- Canopy Hills Villas Phuket: club-style estate of 9 view hillside villas in Ko Kaeo, close to BISP and other international schools.
- Positioning: large family homes for everyday island living, not generic holiday rental villas.
- Core value: open views over the green valley, lakes, hills and sunset; school-side Phuket living; spacious homes; quiet green surroundings; privacy; quality materials; thermal/sound insulation; storage; family layouts.
- Location logic: schools, Central, marinas, golf and everyday infrastructure nearby, away from tourist bustle.
- Villa types: L-size 650 sqm and XL-size 750 sqm for general messaging; for a specific villa, use exact villa-level data from the current SalesKit price list. Villa configurations include 4+1 and 5+1 bedrooms.
- Price context: from approx. THB 57.5M; standard agency commission is 6%.
- Timelines: C9 in August 2026; C6, C7 and C8 in August 2027; the whole project by the end of 2027. Do not invent more exact dates.
- SalesKit: https://drive.google.com/drive/folders/1oSpCppxgLdRXUrHyxn8tFftyPLB4PiP5
- Verified SalesKit client presentations: EN `Canopy Hills  ENG.pdf` https://drive.google.com/file/d/1c1djBre5fRbmeoLXPsLYAczRFFIXbUvL/view | RU `Canopy Hills  RUS.pdf` https://drive.google.com/file/d/1jlBF9tc1mtX-ygI1kletcuqf9skex58T/view | CH `Canopy Hills CH.pdf` https://drive.google.com/file/d/1bgW4eOAdl_Zh_MTeoQAijaiq5Bn8IOhO/view
- Price list: https://drive.google.com/file/d/16nxg2ShVpBVuyMQ6Ajwxvr-iNcagar6l/view. The current price list inside the SalesKit is the source of truth for availability and prices.
- Never substitute the flipbook/book mockup for a client presentation in WhatsApp.
- Current company details: Hugs Management Co., Ltd., Reg. No. 0835566030613, address 99/101, Moo.2, Koh Keaw Sub District, Mueang District, Phuket Province, 83000, Thailand.
- Common area fee: 20 THB per sqm.

Current agent knowledge policy:
- Treat Canopy as: "9 hillside view villas near BISP and other international schools for families and long-term Phuket living." Lead with this definition when project context is needed.
- Russian wording must be natural: do not write "для семей и долгосрочного проживания", "идеально для семей и долгосрочного проживания", "для семьи с пожилыми и взрослыми детьми", or "стиль жизни". Use "для семей с детьми", "для постоянной жизни на Пхукете", "для клиентов, которым важны тишина, вид, простор и удобная локация", and keep the English word "lifestyle" when that concept is needed in Russian.
- Useful client profiles: families relocating to Phuket, school-side long-term rental investors, buyers focused on view/space/quiet, and lifestyle buyers who value marinas, golf, Central and daily infrastructure.
- Direct client strategy: for a generic direct buyer request, send a short client intro plus the presentation link in the language of the conversation, then qualify gently. Identify whether the client is (1) family with school-age children, (2) lifestyle/permanent-living buyer without school as the main driver, or (3) investor. Then highlight the matching Canopy advantages instead of dumping generic materials.
- Family with school-age children: highlight BISP/other international schools, quiet green location, family layouts, storage, bedrooms, 650/750 sqm scale, long-term living. Ask about relocation timing, school area, and bedrooms only when useful.
- Lifestyle/permanent-living buyer: highlight hillside views, quiet green surroundings, privacy, Central Phuket, marinas, golf, daily infrastructure, and Ko Kaeo as a practical island location. Ask whether it is full-time residence or second home, and what matters most: view, privacy, marinas/golf, or infrastructure.
- Investor/direct investment buyer: use only the approved soft thesis: stable long-term rental demand from international-school families and scarcity of unique view residences for permanent living. Ask about long-term rental demand, capital preservation, personal use with rental potential, and investment horizon. Do not promise ROI.
- Use the current SalesKit price list for availability and prices. Do not use old 2025 price lists, old villa offers, old payment-plan files, or old special-offer language.
- Use soft investment logic only: stable long-term rental demand from international-school families, and Canopy's uniqueness as residences for permanent living with views. Do not promise ROI or repeat hard claims such as 15% annual appreciation, 30% below market, or any "below market" claim.
- Approved documents/media that may be sent when relevant: anything inside the SalesKit as a full folder link or individual files, EN/RU/CH presentations from SalesKit, current SalesKit price list, approved intro video and advantage visuals, layout links/pages, standard/current agency agreement template, and company/DBD corporate registration documents needed for agency agreement. Old invitation/show-unit materials are stale; do not send them as a separate default package.
- Manual-only documents: villa-specific offers, payment plans/payment details, NDA/investor documents, and legal/DD documents including chanotes, permits, title documents, sale/lease agreement samples, common-area legal documents and legal structure advice.
- Legal basics allowed in WhatsApp: the land plot is owned by Hugs Management Co., Ltd.; each villa plot has its own separate land title; leasehold and freehold structures can be discussed depending on the buyer and transaction structure. Do not send title/chanote/DD documents or give detailed legal advice automatically; offer a project-team/legal discussion for detailed review.
- Agency agreement workflow: when an agent wants to cooperate or asks for an agency agreement, collect company legal name, registration number, registered address, authorized representative name/title, phone, email, and DBD/company registration documents. Standard commission is 6%. Codex prepares the filled agreement from the current master template; the WhatsApp agent should collect data and route the task, not improvise legal text.
- Do not use stale/rejected wording: "камерный", "intro-pack", "carousel" to clients/agents, "video below", "emotional context", "real progress not only renders", "strong engineering quality", "materials for your database", "special conditions until September", or old 2025 discounts/prices.

Agreed agent welcome standard:
- For an agent/broker/materials request, the standard first package is: intro video with a short language-matched caption and language-matched advantages carousel.
- Do not treat a generic "more information/details" message as an agent request. If the role is unclear, first ask whether they are considering the villa for themselves/family or representing a client; do not send the agent welcome pack yet.
- The video caption uses the approved agent text: 9 view villas on a hillside, school/infrastructure location, family living, views, privacy, quality materials, sound/thermal insulation and storage.
- The carousel carries concrete numbers and advantages, including investment appeal based on long-term rental demand from international-school families and the scarcity of unique view projects. The L-size and XL-size layout cards link to grouped layout pages with C1-C9 source layout files; do not send separate layout PDF documents by default.
- The system has a tool named send_agent_welcome_pack. When the message is a real agent/broker/materials scenario, give a short context-aware AI reply first; the pack may then be sent as supporting material, never as a substitute for understanding the message.
- Do not say files/media were sent unless you include a link in your reply or the tool context says the system will send the pack.
- Do not ask "specific client or materials for database" as a default question. For agents, send the agreed pack first; after that, only ask a next-step question if the agent responds with a concrete client, viewing, budget, registration or commission issue.

Dialogue rules:
- Do not overpromise ROI, legal outcomes, immigration outcomes, completion dates beyond the stated C9 preview window, or availability.
- Think like a trained Phuket real-estate sales agent, not like a scripted bot: infer the role and intent from the message, use the agreed project positioning, and answer the actual next step.
- Keep the conversation friendly, easy and alive. Be добродушный: light, calm, helpful, with a little humor where natural. Stay professional and precise when the topic is legal, price, investment, or negotiations.
- Ask a qualifying question only when it naturally moves the current conversation forward. Do not ask generic branch questions after the role is already clear.
- For direct clients: first identify family/school, lifestyle/permanent-living, or investor logic; then highlight the relevant Canopy advantages. Do not ask many questions at once.
- For legal basics, it is allowed to state Hugs Management ownership, separate land title for each villa plot, and possible leasehold/freehold discussion. For legal/DD documents, contracts, detailed structure advice, investor docs, discount, payment-plan, villa-specific offer, or serious negotiation topics: acknowledge and escalate to Vladimir/Andrey or a short call.
- For agents: the first move is the agreed welcome pack. Do not ask whether they have a specific client or need materials for their database. After the pack, respond to the agent's actual reply: registration details, viewing timing, client profile, commission, availability, or a call.
- If the conversation has already identified the person as an agent/broker, keep treating following messages as agent context until test mode ends or the role clearly changes. If they then describe a client profile, answer as to an agent about that client; do not switch to direct-client mode.
- For an agent with a client profile such as elderly buyers, adult children, a big house, secluded/quiet location: say the project may fit clients who value a large private home, open views, quiet green surroundings, privacy, and convenient access to marinas, golf, Central and daily infrastructure. Do not mention schools unless relevant.
- If an identified agent says a short command like "Подготовь", infer it refers to preparing the relevant response/material for the just-discussed client profile. Do not ask "what exactly should I prepare?" unless there is no usable prior context.
- For Vladimir/operator messages: behave as an internal AI teammate unless `operator_test_mode` is true in metadata. If `operator_test_mode` is true, treat the incoming message as a simulated inbound lead/agent message and answer as the sales assistant being tested.
- When Vladimir exits test mode, his next WhatsApp messages are working instructions/feedback. Answer them in WhatsApp as Codex/internal teammate, not as a sales lead. Do not send lead materials or sales tools in work mode.
- Do not auto-send sales templates, carousels or welcome packs to Vladimir unless test mode/tool context explicitly allows that exact pack.
- Mention 6% commission only when commission/cooperation is relevant or the agent asks about terms.
- For client registration: ask for client name and partial phone number. Registration is indefinite. If useful, also collect villa preference and timing, but do not make them mandatory.
- For quotation, reservation, payment plan, special price, villa-specific offer, legal due diligence, sale/lease agreement samples, NDA/investor documents, or client registration confirmation: do not improvise; gather only the missing practical detail and escalate.
- For route/viewing logistics: send the location pin only after viewing timing is confirmed. The correct access instruction is to enter through the soi next to The Big Bear Kitchen; avoid generic Google route assumptions.
- For irrelevant/spam messages: politely ask them to clarify if this is about purchasing or representing a client for Canopy Hills.
- Do not claim you sent files/media unless the message explicitly includes a link you are providing.
- Max {AI_AGENT_MAX_CHARS} characters.
"""


def extract_response_text(data):
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    chunks = []
    for output in data.get("output", []) or []:
        for content in output.get("content", []) or []:
            text = content.get("text")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def openai_response_text(system_prompt, user_text, metadata):
    access_token = os.environ.get("OPENAI_API_KEY", "").strip()
    if not access_token:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": AI_AGENT_MODEL,
        "input": (
            f"{system_prompt}\n\n"
            f"Conversation metadata:\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
            f"Incoming WhatsApp message:\n{user_text}"
        ),
        "max_output_tokens": 450,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    text = extract_response_text(data)
    if not text:
        raise RuntimeError(f"OpenAI response did not contain text: {json.dumps(data, ensure_ascii=False)[:1000]}")
    return text[:AI_AGENT_MAX_CHARS].strip()


def log_ai_agent_event(wa_id, inbound_message_id, status, reply="", error=""):
    con = db()
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_id TEXT NOT NULL,
            inbound_message_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reply TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT INTO ai_agent_events
          (wa_id, inbound_message_id, status, reply, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (wa_id, inbound_message_id, status, reply, error, utc_now()),
    )
    con.commit()
    con.close()


def recent_inbound_context(wa_id, limit=8):
    con = db()
    rows = con.execute(
        """
        SELECT text, message_type, received_at
        FROM messages
        WHERE wa_id = ? AND direction = 'inbound'
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (wa_id, limit),
    ).fetchall()
    con.close()
    history = []
    for row in reversed(rows):
        text = (row["text"] or "").strip()
        if not text:
            text = f"[{row['message_type']} message]"
        history.append({"at": row["received_at"], "text": text[:600]})
    return history


def run_ai_agent_reply(item, classification):
    is_operator = item.get("wa_id") in AI_OPERATOR_WA_IDS
    tool_plan = ai_agent_tool_plan(item, classification)
    metadata = {
        "wa_id": item.get("wa_id"),
        "profile_name": item.get("profile_name"),
        "segment": classification.get("segment"),
        "priority": classification.get("priority"),
        "next_action": classification.get("next_action"),
        "is_operator": is_operator,
        "operator_test_mode": bool(item.get("operator_test_mode")),
        "tool_plan": tool_plan,
        "recent_inbound_context": recent_inbound_context(item.get("wa_id"), 8),
    }
    reply = openai_response_text(
        ai_agent_system_prompt(is_operator=is_operator),
        item.get("text") or "",
        metadata,
    )
    if AI_AGENT_DRY_RUN:
        log_ai_agent_event(item["wa_id"], item["message_id"], "dry_run", reply, "")
        return reply
    send_whatsapp_text(item["wa_id"], reply)
    tool_results = run_ai_agent_tools(item, classification, tool_plan)
    if tool_results:
        reply = f"{reply}\n\n[tools] {json.dumps(tool_results, ensure_ascii=False)}"
    log_ai_agent_event(item["wa_id"], item["message_id"], "sent", reply, "")
    return reply


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
        item["operator_test_mode"] = False
        classification = classify(item["text"])
        cur = con.execute(
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
        inserted = cur.rowcount > 0
        developer_research_contact = update_developer_research_from_inbound(con, item, now)
        mark_whatsapp_message_read(item["message_id"])
        if developer_research_contact:
            continue
        mode_reply = operator_test_mode_command(con, item)
        if mode_reply:
            con.commit()
            if inserted:
                try:
                    send_whatsapp_text(item["wa_id"], mode_reply)
                    log_ai_agent_event(item["wa_id"], item["message_id"], "operator_mode", mode_reply, "")
                except Exception as exc:
                    log_ai_agent_event(item["wa_id"], item["message_id"], "operator_mode_error", "", str(exc))
                    print(f"operator mode reply failed for {item['wa_id']}: {exc}")
            continue
        if item.get("wa_id") in AI_OPERATOR_WA_IDS and get_operator_mode(con, item["wa_id"]) == "lead_test":
            item["operator_test_mode"] = True
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
        if inserted:
            autoreply = generate_test_autoreply(item)
            if autoreply:
                try:
                    send_whatsapp_text(item["wa_id"], autoreply)
                except Exception as exc:
                    print(f"test autoreply failed for {item['wa_id']}: {exc}")
            elif item.get("message_type") == "audio":
                con.commit()
                try:
                    process_audio_message_for_ai(item["message_id"], item)
                except Exception as exc:
                    log_ai_agent_event(item["wa_id"], item["message_id"], "audio_error", "", str(exc))
                    print(f"ai audio processing failed for {item['wa_id']}: {exc}")
            elif should_ai_agent_reply(item, classification):
                con.commit()
                try:
                    run_ai_agent_reply(item, classification)
                except Exception as exc:
                    log_ai_agent_event(item["wa_id"], item["message_id"], "error", "", str(exc))
                    print(f"ai agent reply failed for {item['wa_id']}: {exc}")
    con.commit()
    con.close()


def mark_whatsapp_message_read(message_id):
    if not message_id:
        return {"ok": False, "error": "message_id is empty"}
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        return {"ok": True, "meta": send_whatsapp_payload(payload)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_whatsapp_text(to, body):
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": True, "body": body},
    }
    response = send_whatsapp_payload(payload)
    store_outbound_message(to, "text", body, response, "Outbound text sent from bridge.")
    return response


CANOPY_MARKET_INTEL_OUTREACH_20260617 = [
    {
        "developer": "Botanica Luxury Villas",
        "to": "66983947097",
        "text": """Hi Botanica team, this is Vladimir from Hugs Management in Phuket.

We have an agency division and are currently updating our internal developer database and training materials for our property agents.

Could you please share your current broker/agent pack for Botanica villas, including brochure, price list, availability, payment schedule, client registration rules and commission/cooperation terms?

Also, who is the right sales contact for agent cooperation and site inspections?

Thank you.""",
    },
    {
        "developer": "Anchan Villas",
        "to": "66923899000",
        "text": """Hi Anchan Villas team, this is Vladimir from Hugs Management in Phuket.

We have an agency division and are updating our internal developer database and agent training materials for Phuket villa projects.

Could you please share your current broker/agent materials for Anchan Villas: brochure, price list, availability, payment schedule, client registration rules and commission/cooperation terms?

Please also let me know who is the best contact for agent cooperation and site inspections.

Thank you.""",
    },
    {
        "developer": "Trichada Villas",
        "to": "66945933980",
        "text": """Hi Trichada team, this is Vladimir from Hugs Management in Phuket.

We have an agency division and are updating our internal developer database and agent training materials, especially for family-oriented villa projects.

Could you please share your current broker/agent materials: brochure, price list, availability, payment schedule, client registration rules and commission/cooperation terms?

Please also let me know the right contact for agent cooperation and site inspections.

Thank you.""",
    },
    {
        "developer": "Andaman Asset Solution / The Trinity Village",
        "to": "66618190731",
        "text": """Hi Andaman Asset Solution team, this is Vladimir from Hugs Management in Phuket.

We have an agency division and are updating our internal developer database and agent training materials for Phuket villa projects.

Could you please share your current broker/agent materials for The Trinity Village and any other active villa projects: brochure, price list, availability, payment schedule, client registration rules and commission/cooperation terms?

Please also let me know who handles agent cooperation and site inspections.

Thank you.""",
    },
    {
        "developer": "Mouana Phuket",
        "to": "66801468234",
        "text": """Hi Mouana team, this is Vladimir from Hugs Management in Phuket.

We have an agency division and are updating our internal developer database and agent training materials for Phuket villa projects.

Could you please share your current broker/agent materials: brochure, price list, availability, payment schedule, client registration rules and commission/cooperation terms?

Please also let me know who is the right contact for agent cooperation and site inspections.

Thank you.""",
    },
]


def send_canopy_market_intel_outreach_20260617():
    if CANOPY_MARKET_INTEL_BATCH_MARKER.exists():
        return {
            "ok": False,
            "already_sent": True,
            "marker": str(CANOPY_MARKET_INTEL_BATCH_MARKER),
        }

    results = []
    for item in CANOPY_MARKET_INTEL_OUTREACH_20260617:
        try:
            meta = send_whatsapp_text(item["to"], item["text"])
            results.append(
                {
                    "ok": True,
                    "developer": item["developer"],
                    "to": item["to"],
                    "meta": meta,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "developer": item["developer"],
                    "to": item["to"],
                    "error": str(exc),
                }
            )

    payload = {
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    if all(item.get("ok") for item in results):
        CANOPY_MARKET_INTEL_BATCH_MARKER.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": all(item.get("ok") for item in results),
        "already_sent": False,
        "marker": str(CANOPY_MARKET_INTEL_BATCH_MARKER),
        "results": results,
    }


def send_whatsapp_template(to, template_name, language_code="en_US", components=None):
    template = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }
    response = send_whatsapp_payload(payload)
    label = f"template:{template_name}:{language_code}"
    store_outbound_message(to, "template", label, response, "Outbound template sent from bridge.")
    return response


def send_whatsapp_media(to, media_type, link, caption="", filename=""):
    if media_type not in {"image", "video", "document"}:
        raise ValueError("media_type must be one of: image, video, document")
    media = {"link": link}
    if caption:
        media["caption"] = caption
    if filename and media_type == "document":
        media["filename"] = filename
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": media_type,
        media_type: media,
    }
    response = send_whatsapp_payload(payload)
    label = f"{media_type}:{link}"
    store_outbound_message(to, media_type, label, response, "Outbound media sent from bridge.")
    return response


def is_operator_instruction(text):
    t = (text or "").lower()
    instruction_terms = [
        "давай здесь вернемся",
        "мы должны",
        "стандарт",
        "согласованное сообщение",
        "натаскай",
        "обнови",
        "зашей",
        "инструменты",
        "плейбук",
        "логика",
    ]
    return has_any(t, instruction_terms)


def is_agent_materials_scenario(text):
    t = (text or "").lower()
    if is_operator_instruction(t):
        return False
    agent_terms = [
        "agent",
        "broker",
        "agency",
        "realtor",
        "commission",
        "co-broker",
        "cooperate",
        "cooperation",
        "client registration",
        "register client",
        "my client",
        "for client",
        "for my client",
        "representing a client",
        "агент",
        "брокер",
        "риэлтор",
        "комисс",
        "сотруднич",
        "регистрация клиента",
        "зарегистрировать клиента",
        "для клиента",
        "мой клиент",
        "представляю клиента",
        "материалы для базы",
    ]
    material_terms = [
        "sales kit",
        "saleskit",
        "presentation",
        "deck",
        "brochure",
        "send materials",
        "send details",
        "project details",
        "materials",
        "презентац",
        "материал",
        "подроб",
        "информац",
    ]
    return has_any(t, agent_terms)


def agent_intro_video_caption(language="en"):
    if language == "ru":
        return """Canopy Hills Villas - клубный поселок из 9 видовых вилл на холме в Ko Kaeo, рядом с BISP и другими международными школами. Из вилл открываются виды на зеленую долину, озера, холмы и закат.

Проект хорошо подходит семьям с детьми и клиентам, которые живут на Пхукете или планируют переезд: рядом школы, Central, марины, гольф и вся повседневная инфраструктура. При этом локация спокойная, зеленая и без туристической суеты.

Это большой семейный дом для жизни, а не вилла на отпуск: просторные планировки, приватность, качественные материалы, хорошая шумо- и теплоизоляция, много места для хранения и повседневного быта.

Такой объект стоит предлагать клиентам, которым важны вид, пространство, тишина, удобная локация и уровень дома выше обычных поселков рядом со школами.

Full Sales Kit:
https://drive.google.com/drive/folders/1oSpCppxgLdRXUrHyxn8tFftyPLB4PiP5"""
    return """Canopy Hills Villas is a club-style estate of 9 view villas on a hillside in Ko Kaeo, close to BISP and other international schools. The villas open to views of the green valley, lakes, hills and sunset.

The project is a strong fit for families with children and clients who live in Phuket or plan to relocate: schools, Central, marinas, golf and everyday infrastructure are all nearby, while the location remains quiet, green and away from tourist areas.

This is a large family home for everyday island living, not a holiday villa: spacious layouts, privacy, quality materials, strong sound and thermal insulation, generous storage and practical spaces for daily family life.

It is worth offering to clients who value views, space, quiet surroundings, convenient location and a higher level of home than standard housing estates near the schools.

Full Sales Kit:
https://drive.google.com/drive/folders/1oSpCppxgLdRXUrHyxn8tFftyPLB4PiP5"""


def agent_carousel_template(language="en"):
    if language == "ru":
        return "canopy_agent_advantages_carousel_10_v7", "ru"
    return "canopy_agent_advantages_carousel_10_v7", "en_US"


def send_agent_carousel_v7(to, language="en"):
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v3_01_private_hillside_estate.jpg",
        "carousel_v3_07_real_view.jpg",
        "carousel_v3_02_usable_large_plots.jpg",
        "carousel_v3_03_real_family_scale.jpg",
        "carousel_v3_04_7m_living_room.jpg",
        "carousel_v3_05_investment_bisp.jpg",
        "carousel_v3_06_green_district.jpg",
        "carousel_v3_08_heat_noise_insulation.jpg",
        "carousel_v3_09_villa_l_layout.jpg",
        "carousel_v3_10_villa_xl_layout.jpg",
    ]
    components = [
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    return send_whatsapp_template(
        to,
        *agent_carousel_template(language),
        components,
    )


def send_agent_carousel_v6(to):
    return send_agent_carousel_v7(to, "en")


def send_agent_intro_video(to, language="en"):
    return send_whatsapp_media(
        to,
        "video",
        f"{BASE_URL}/assets/agent_intro_video.mp4",
        agent_intro_video_caption(language),
    )


def agent_layout_document_caption(language="en"):
    if language == "ru":
        return "Читаемая PDF-планировка для увеличения и пересылки клиенту."
    return "Readable PDF layout for zooming and forwarding to a client."


def send_agent_layout_documents(to, language="en"):
    documents = [
        (
            "agent-layout-l",
            "Canopy_Hills_L_size_layout.pdf",
            "Canopy Hills L-size layout.pdf",
        ),
        (
            "agent-layout-xl",
            "Canopy_Hills_XL_size_layout.pdf",
            "Canopy Hills XL-size layout.pdf",
        ),
    ]
    results = []
    for label, asset_name, filename in documents:
        results.append(
            {
                "label": label,
                "meta": send_whatsapp_media(
                    to,
                    "document",
                    f"{BASE_URL}/assets/{asset_name}",
                    agent_layout_document_caption(language),
                    filename,
                ),
            }
        )
    return results


def recent_agent_pack_sent(to):
    con = db()
    row = con.execute(
        """
        SELECT id FROM messages
        WHERE wa_id = ? AND direction = 'outbound'
          AND (
            text LIKE '%quick agent intro%'
            OR text LIKE '%коротко для агента%'
            OR text LIKE 'document:%Canopy_Hills_L_size_layout.pdf%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v7:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v6:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v5:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v4:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v3:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v2:%'
            OR text LIKE 'template:canopy_agent_advantages_carousel_10_v1:%'
            OR text LIKE 'template:canopy_agent_intro_carousel_10_v6:%'
          )
        ORDER BY received_at DESC
        LIMIT 1
        """,
        (to,),
    ).fetchone()
    con.close()
    return bool(row)


def send_agent_welcome_pack(to, language="en"):
    if not AGENT_WELCOME_PACK_APPROVED:
        return [
            {
                "label": "agent-welcome-pack",
                "ok": False,
                "skipped": True,
                "reason": "agent welcome pack text is not approved",
            }
        ]
    results = []
    sends = [
        ("agent-intro-video", lambda: send_agent_intro_video(to, language)),
        ("agent-carousel-v7", lambda: send_agent_carousel_v7(to, language)),
    ]
    for label, send in sends:
        try:
            meta = send()
            if isinstance(meta, list):
                results.extend({"label": item.get("label", label), "ok": True, "meta": item.get("meta", item)} for item in meta)
            else:
                results.append({"label": label, "ok": True, "meta": meta})
        except Exception as exc:
            results.append({"label": label, "ok": False, "error": str(exc)})
    return results


def ai_agent_tool_plan(item, classification):
    if not ENABLE_AI_AGENT_TOOLS:
        return []
    if not AGENT_WELCOME_PACK_APPROVED:
        return []
    text = item.get("text") or ""
    segment = classification.get("segment")
    is_operator = item.get("wa_id") in AI_OPERATOR_WA_IDS
    operator_test_mode = bool(item.get("operator_test_mode"))
    agent_scenario = is_agent_materials_scenario(text)
    if is_operator and not operator_test_mode:
        return []
    if segment not in {"broker", "materials_request", "client_registration"} and not agent_scenario:
        return []
    if not is_operator and recent_agent_pack_sent(item.get("wa_id")):
        return []
    if not agent_scenario and segment != "broker":
        return []
    return [{"tool": "send_agent_welcome_pack", "language": "ru" if is_russian_text(text) else "en"}]


def run_ai_agent_tools(item, classification, tool_plan):
    if not tool_plan or AI_AGENT_DRY_RUN:
        return []
    results = []
    for action in tool_plan:
        if action.get("tool") != "send_agent_welcome_pack":
            continue
        tool_result = send_agent_welcome_pack(
            item["wa_id"],
            action.get("language") or "en",
        )
        results.append({"tool": "send_agent_welcome_pack", "results": tool_result})
    return results


def send_agent_packet_test():
    to = "66628512432"
    text = """Hi Vladimir, quick Canopy Hills agent packet test.

Canopy Hills Villas Phuket is a club estate of 9 premium hillside villas in Ko Kaeo, opposite British International School Phuket.

The project is designed for families relocating to Phuket or living on the island full-time: quiet green hills, panoramic views, spacious interiors, proper storage, large bedrooms, high ceilings, quality engineering and daily infrastructure nearby.

Key points for agents:
- 9-villa private estate on the hill opposite BISP
- Focus: long-term family living, not compact holiday-style villas
- Villa sizes: approx. 650-768 sqm built-up
- 4+1 and 5+1 bedroom layouts
- Land plots: approx. 670-1,214 sqm
- Views: hills, valley, lake / BISP area and sunsets
- Private pools: 12x3.5m or 15x3.5m
- Current availability to discuss: C1, C2, C3 and C6
- C9: nearly ready, private viewings expected in early/mid August
- C7-C8: construction started
- Price range: approx. THB 57.5M-76.6M / USD 1.74M-2.32M
- Agent commission: 6%

Location:
- British International School Phuket: opposite / approx. 3 min
- Boat Lagoon / Royal Phuket Marina: approx. 7 min
- Loch Palm Golf Club: approx. 7 min
- Central Phuket / hospitals / supermarkets: approx. 10-15 min
- Bang Tao: approx. 20 min

This is a strong fit for expat families prioritizing school access, space, privacy, views and a near-ready product away from the tourist zones.

Website: https://canopy.villas
ENG presentation: https://drive.google.com/file/d/1c1djBre5fRbmeoLXPsLYAczRFFIXbUvL/view
Price list: https://drive.google.com/file/d/16nxg2ShVpBVuyMQ6Ajwxvr-iNcagar6l/view

If you have a client focused on BISP / long-term family living, we can pre-arrange a private C9 viewing for early/mid August."""
    results = []
    render_links = [
        "https://drive.google.com/uc?export=download&id=12oifyEV0kgHLomQM2mI211qXEy9hDEC2",
        "https://drive.google.com/uc?export=download&id=1GDlGbsbUiyBeBYcOpsJGKkI6I34E3pK6",
    ]
    sends = [
        *[
            (
                f"image-{index}",
                lambda link=link: send_whatsapp_media(
                    to,
                    "image",
                    link,
                ),
            )
            for index, link in enumerate(render_links, start=1)
        ],
        (
            "video",
            lambda: send_whatsapp_media(
                to,
                "video",
                "https://drive.google.com/uc?export=download&id=16VkPGXWCzin07aW4tV9mzbXKhVYOqSpk",
            ),
        ),
        ("text", lambda: send_whatsapp_text(to, text)),
    ]
    for label, send in sends:
        try:
            results.append({"label": label, "ok": True, "meta": send()})
        except Exception as exc:
            results.append({"label": label, "ok": False, "error": str(exc)})
    return results


def send_delivery_ping_test():
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "delivery test"}],
        }
    ]
    result = send_whatsapp_template(
        "66628512432",
        "canopy_broker_preview_august",
        "en_US",
        components,
    )
    return [{"label": "approved-template", "ok": True, "meta": result}]


def send_agent_carousel_v2_test():
    to = "66628512432"
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v2_01_private_hillside_estate.jpg",
        "carousel_v2_02_large_land_plots.jpg",
        "carousel_v2_03_usable_garden.jpg",
        "carousel_v2_04_family_scale.jpg",
        "carousel_v2_05_7m_living_room.jpg",
        "carousel_v2_06_kitchens_bbq.jpg",
        "carousel_v2_07_real_view.jpg",
        "carousel_v2_08_heat_noise_insulation.jpg",
    ]
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_intro_carousel_8_v2",
        "en_US",
        components,
    )
    return [{"label": "agent-carousel-v2", "ok": True, "meta": result}]


def send_agent_carousel_v3_test():
    to = "66628512432"
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v3_01_private_hillside_estate.jpg",
        "carousel_v3_02_usable_large_plots.jpg",
        "carousel_v3_03_real_family_scale.jpg",
        "carousel_v3_04_7m_living_room.jpg",
        "carousel_v3_05_kitchens_bbq.jpg",
        "carousel_v3_06_green_district.jpg",
        "carousel_v3_07_real_view.jpg",
        "carousel_v3_08_heat_noise_insulation.jpg",
        "carousel_v3_09_villa_l_layout.jpg",
        "carousel_v3_10_villa_xl_layout.jpg",
    ]
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_intro_carousel_10_v3",
        "en_US",
        components,
    )
    return [{"label": "agent-carousel-v3", "ok": True, "meta": result}]


def send_agent_carousel_v4_test():
    to = "66628512432"
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v3_01_private_hillside_estate.jpg",
        "carousel_v3_02_usable_large_plots.jpg",
        "carousel_v3_03_real_family_scale.jpg",
        "carousel_v3_04_7m_living_room.jpg",
        "carousel_v3_05_kitchens_bbq.jpg",
        "carousel_v3_06_green_district.jpg",
        "carousel_v3_07_real_view.jpg",
        "carousel_v3_08_heat_noise_insulation.jpg",
        "carousel_v3_09_villa_l_layout.jpg",
        "carousel_v3_10_villa_xl_layout.jpg",
    ]
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_intro_carousel_10_v4",
        "en_US",
        components,
    )
    return [{"label": "agent-carousel-v4", "ok": True, "meta": result}]


def send_agent_carousel_v5_test():
    to = "66628512432"
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v3_01_private_hillside_estate.jpg",
        "carousel_v3_02_usable_large_plots.jpg",
        "carousel_v3_03_real_family_scale.jpg",
        "carousel_v3_04_7m_living_room.jpg",
        "carousel_v3_05_kitchens_bbq.jpg",
        "carousel_v3_06_green_district.jpg",
        "carousel_v3_07_real_view.jpg",
        "carousel_v3_08_heat_noise_insulation.jpg",
        "carousel_v3_09_villa_l_layout.jpg",
        "carousel_v3_10_villa_xl_layout.jpg",
    ]
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_intro_carousel_10_v5",
        "en_US",
        components,
    )
    return [{"label": "agent-carousel-v5", "ok": True, "meta": result}]


def send_agent_carousel_v6_test():
    to = "66628512432"
    base = f"{BASE_URL}/assets"
    image_names = [
        "carousel_v3_01_private_hillside_estate.jpg",
        "carousel_v3_02_usable_large_plots.jpg",
        "carousel_v3_03_real_family_scale.jpg",
        "carousel_v3_04_7m_living_room.jpg",
        "carousel_v3_05_kitchens_bbq.jpg",
        "carousel_v3_06_green_district.jpg",
        "carousel_v3_07_real_view.jpg",
        "carousel_v3_08_heat_noise_insulation.jpg",
        "carousel_v3_09_villa_l_layout.jpg",
        "carousel_v3_10_villa_xl_layout.jpg",
    ]
    components = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
        {
            "type": "carousel",
            "cards": [
                {
                    "card_index": index,
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {"link": f"{base}/{name}"},
                                }
                            ],
                        }
                    ],
                }
                for index, name in enumerate(image_names)
            ],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_intro_carousel_10_v6",
        "en_US",
        components,
    )
    return [{"label": "agent-carousel-v6", "ok": True, "meta": result}]


def send_agent_intro_video_test():
    to = "66628512432"
    caption = (
        "Canopy Hills Villas Phuket - a private hillside estate opposite BISP, "
        "designed for long-term family living.\n\n"
        "Below is a compact carousel with key advantages and villa formats for agents and relevant clients."
    )
    result = send_whatsapp_media(
        to,
        "video",
        f"{BASE_URL}/assets/agent_intro_video.mp4",
        caption,
    )
    return [{"label": "agent-intro-video", "ok": True, "meta": result}]


def send_agent_video_cta_template_test():
    to = "66628512432"
    components = [
        {
            "type": "header",
            "parameters": [
                {
                    "type": "video",
                    "video": {"link": f"{BASE_URL}/assets/agent_intro_video.mp4"},
                }
            ],
        },
        {
            "type": "body",
            "parameters": [{"type": "text", "text": "there"}],
        },
    ]
    result = send_whatsapp_template(
        to,
        "canopy_agent_video_intro_cta_v1",
        "en_US",
        components,
    )
    return [{"label": "agent-video-cta", "ok": True, "meta": result}]


def send_whatsapp_payload(payload):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    if not access_token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not set")
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            response = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    return response


def graph_get_json(path):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    if not access_token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")
    url = f"https://graph.facebook.com/{graph_version}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def download_whatsapp_media(media_id, fallback_url=""):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")
    media_url = fallback_url
    if media_id:
        media_meta = graph_get_json(media_id)
        media_url = media_meta.get("url", "") or media_url
    if not media_url:
        raise RuntimeError("No media url is available for this WhatsApp audio")
    req = urllib.request.Request(
        media_url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            content_type = res.headers.get("Content-Type", "application/octet-stream")
            return res.read(), content_type
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def multipart_form_data(fields, files):
    boundary = f"----canopy-{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, file_info in files.items():
        filename, content_type, body = file_info
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks.append(body)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def transcribe_audio_bytes(audio_bytes, content_type):
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip()
    extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".ogg"
    body, request_content_type = multipart_form_data(
        {
            "model": model,
            "response_format": "text",
            "language": os.environ.get("OPENAI_TRANSCRIBE_LANGUAGE", "ru"),
            "prompt": (
                "This is a WhatsApp voice note from Vladimir about Canopy Hills "
                "Villas Phuket, real estate sales, agents, clients, BISP, Ko Kaeo, "
                "Phuket market strategy, villas C1-C9. Preserve useful business details."
            ),
        },
        {
            "file": (
                f"whatsapp_voice{extension}",
                content_type or "audio/ogg",
                audio_bytes,
            )
        },
    )
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": request_content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.read().decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(error_body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def transcribe_audio_message(message_id):
    con = db()
    row = con.execute(
        "SELECT * FROM messages WHERE id = ? AND direction = 'inbound'",
        (message_id,),
    ).fetchone()
    if not row:
        con.close()
        raise RuntimeError("Audio message was not found")
    raw = json.loads(row["raw_json"])
    if raw.get("type") != "audio":
        con.close()
        raise RuntimeError("Message is not an audio message")
    audio = raw.get("audio", {})
    audio_bytes, content_type = download_whatsapp_media(
        audio.get("id", ""),
        audio.get("url", ""),
    )
    transcript = transcribe_audio_bytes(audio_bytes, content_type)
    classification = classify(transcript)
    now = utc_now()
    con.execute(
        """
        UPDATE messages
        SET text = ?
        WHERE id = ?
        """,
        (f"[voice transcription]\n{transcript}", message_id),
    )
    con.execute(
        """
        INSERT INTO contacts
          (wa_id, profile_name, segment, priority, last_message_at,
           escalation_required, next_action, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(wa_id) DO UPDATE SET
          segment = excluded.segment,
          priority = excluded.priority,
          last_message_at = excluded.last_message_at,
          escalation_required = excluded.escalation_required,
          next_action = excluded.next_action,
          updated_at = excluded.updated_at
        """,
        (
            row["wa_id"],
            "",
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
    return {
        "message_id": message_id,
        "wa_id": row["wa_id"],
        "transcript": transcript,
        "classification": classification,
        "content_type": content_type,
        "bytes": len(audio_bytes),
    }


def transcribe_latest_audio_for(wa_id):
    con = db()
    row = con.execute(
        """
        SELECT id FROM messages
        WHERE wa_id = ? AND direction = 'inbound' AND message_type = 'audio'
        ORDER BY received_at DESC
        LIMIT 1
        """,
        (wa_id,),
    ).fetchone()
    con.close()
    if not row:
        raise RuntimeError("No inbound audio messages found for this wa_id")
    return transcribe_audio_message(row["id"])


def process_audio_message_for_ai(message_id, original_item=None):
    if not ENABLE_AI_AUDIO_TRANSCRIPTION:
        return {"message_id": message_id, "ai_reply": "", "skipped": "audio transcription disabled"}
    result = transcribe_audio_message(message_id)
    voice_item = {
        "message_id": message_id,
        "wa_id": result["wa_id"],
        "profile_name": (original_item or {}).get("profile_name", ""),
        "message_type": "text",
        "text": f"[WhatsApp voice note transcript]\n{result['transcript']}",
        "raw": (original_item or {}).get("raw", {}),
        "operator_test_mode": (original_item or {}).get("operator_test_mode", False),
    }
    if not should_ai_agent_reply(voice_item, result["classification"]):
        return {**result, "ai_reply": "", "skipped": "ai agent disabled or filtered"}
    reply = run_ai_agent_reply(voice_item, result["classification"])
    return {**result, "ai_reply": reply}


def process_latest_audio_for(wa_id):
    con = db()
    row = con.execute(
        """
        SELECT id FROM messages
        WHERE wa_id = ? AND direction = 'inbound' AND message_type = 'audio'
        ORDER BY received_at DESC
        LIMIT 1
        """,
        (wa_id,),
    ).fetchone()
    con.close()
    if not row:
        raise RuntimeError("No inbound audio messages found for this wa_id")
    return process_audio_message_for_ai(row["id"])


def store_outbound_message(to, message_type, text, response, next_action):
    now = utc_now()
    message_id = ""
    if response.get("messages"):
        message_id = response["messages"][0].get("id", "")
    con = db()
    con.execute(
        """
        INSERT OR IGNORE INTO messages
          (id, wa_id, direction, message_type, text, raw_json, received_at)
        VALUES (?, ?, 'outbound', ?, ?, ?, ?)
        """,
        (
            message_id or f"outbound:{to}:{now}",
            to,
            message_type,
            text,
            json.dumps(response, ensure_ascii=False),
            now,
        ),
    )
    if is_developer_research_wa_id(to):
        con.commit()
        con.close()
        log_developer_research_outbound(to, text, response, now)
        return
    con.execute(
        """
        INSERT INTO contacts
          (wa_id, profile_name, segment, priority, last_message_at,
           escalation_required, next_action, updated_at)
        VALUES (?, '', 'outbound_test', 'P3', ?, 0, ?, ?)
        ON CONFLICT(wa_id) DO UPDATE SET
          last_message_at = excluded.last_message_at,
          updated_at = excluded.updated_at
        """,
        (to, now, next_action, now),
    )
    con.commit()
    con.close()
    log_developer_research_outbound(to, text, response, now)


def graph_get(access_token, graph_version, path, query):
    url = f"https://graph.facebook.com/{graph_version}/{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def safe_graph_get(access_token, graph_version, path, query):
    try:
        return {"ok": True, "data": graph_get(access_token, graph_version, path, query)}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        try:
            return {"ok": False, "meta_error": json.loads(error_body)}
        except json.JSONDecodeError:
            return {"ok": False, "meta_error": error_body[:500]}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc.reason)}


def graph_post(access_token, graph_version, path, payload):
    url = f"https://graph.facebook.com/{graph_version}/{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def safe_graph_post(access_token, graph_version, path, payload):
    try:
        return {"ok": True, "data": graph_post(access_token, graph_version, path, payload)}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        try:
            return {"ok": False, "meta_error": json.loads(error_body)}
        except json.JSONDecodeError:
            return {"ok": False, "meta_error": error_body[:500]}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc.reason)}


def graph_upload_file_handle(access_token, graph_version, app_id, file_path):
    path = Path(file_path)
    file_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    file_size = path.stat().st_size
    start_query = urlencode(
        {
            "file_name": path.name,
            "file_length": str(file_size),
            "file_type": file_type,
        }
    )
    start_url = f"https://graph.facebook.com/{graph_version}/{app_id}/uploads?{start_query}"
    start_req = urllib.request.Request(
        start_url,
        headers={"Authorization": f"OAuth {access_token}"},
        method="POST",
    )
    with urllib.request.urlopen(start_req, timeout=30) as res:
        session = json.loads(res.read().decode("utf-8"))

    upload_id = session.get("id")
    if not upload_id:
        raise RuntimeError(f"Meta upload session did not return id: {session}")

    upload_url = f"https://graph.facebook.com/{graph_version}/{upload_id}"
    upload_req = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers={
            "Authorization": f"OAuth {access_token}",
            "file_offset": "0",
            "Content-Type": file_type,
        },
        method="POST",
    )
    with urllib.request.urlopen(upload_req, timeout=60) as res:
        uploaded = json.loads(res.read().decode("utf-8"))

    handle = uploaded.get("h")
    if not handle:
        raise RuntimeError(f"Meta upload did not return header handle: {uploaded}")
    return {"handle": handle, "session": session, "uploaded": uploaded, "file_type": file_type, "file_size": file_size}


def safe_graph_upload_file_handle(access_token, graph_version, app_id, file_path):
    try:
        return {"ok": True, "data": graph_upload_file_handle(access_token, graph_version, app_id, file_path)}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        try:
            return {"ok": False, "meta_error": json.loads(error_body)}
        except json.JSONDecodeError:
            return {"ok": False, "meta_error": error_body[:500]}
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


def canopy_template_payload(template_key):
    sales_kit_url = "https://drive.google.com/drive/folders/1oSpCppxgLdRXUrHyxn8tFftyPLB4PiP5"
    carousel8_buttons = [
        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
        {"type": "QUICK_REPLY", "text": "Ask for details"},
    ]
    carousel_minimal_buttons = [
        {"type": "QUICK_REPLY", "text": "Details"},
    ]
    carousel_minimal_buttons_ru = [
        {"type": "QUICK_REPLY", "text": "Подробнее"},
    ]
    carousel_saleskit_buttons = [
        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
    ]
    carousel_saleskit_buttons_ru = [
        {"type": "URL", "text": "Sales Kit", "url": sales_kit_url},
    ]
    carousel_xl_layout_buttons = [
        {"type": "URL", "text": "XL layouts", "url": f"{BASE_URL}/assets/canopy_layouts_xl.html"},
    ]
    carousel_l_layout_buttons = [
        {"type": "URL", "text": "L layouts", "url": f"{BASE_URL}/assets/canopy_layouts_l.html"},
    ]
    carousel_xl_layout_buttons_ru = [
        {"type": "URL", "text": "XL layouts", "url": f"{BASE_URL}/assets/canopy_layouts_xl.html"},
    ]
    carousel_l_layout_buttons_ru = [
        {"type": "URL", "text": "L layouts", "url": f"{BASE_URL}/assets/canopy_layouts_l.html"},
    ]

    def carousel_image_card(handle_placeholder, text, include_buttons=True, buttons=None):
        components = [
                {
                    "type": "HEADER",
                    "format": "IMAGE",
                    "example": {"header_handle": [handle_placeholder]},
                },
                {"type": "BODY", "text": text},
            ]
        if include_buttons:
            components.append({"type": "BUTTONS", "buttons": buttons or carousel8_buttons})
        return {"components": components}

    templates = {
        "agent_video_intro_cta_v1": {
            "name": "canopy_agent_video_intro_cta_v1",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "HEADER",
                    "format": "VIDEO",
                    "example": {"header_handle": ["__AGENT_INTRO_VIDEO_HANDLE__"]},
                },
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, sharing a quick Canopy Hills Villas agent pack.\n\n"
                        "Canopy Hills is a club estate of 9 premium hillside villas opposite BISP, "
                        "designed for long-term family living in Phuket.\n\n"
                        "Use the buttons below for the Sales Kit, client registration or a private viewing request."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                        {"type": "QUICK_REPLY", "text": "Register client"},
                        {"type": "QUICK_REPLY", "text": "Arrange viewing"},
                    ],
                },
            ],
        },
        "agent_intro_carousel_8_v1": {
            "name": "canopy_agent_intro_carousel_8_v1",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here are 8 quick reasons why Canopy Hills is relevant for "
                        "BISP and long-term family buyers in Phuket."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_ESTATE_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Club estate of 9 premium hillside villas",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_BISP_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Opposite BISP, built for family life",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_SPACIOUS_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Spacious 4+1 and 5+1 villas, 650-768 sqm",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_VIEWS_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Panoramic hill, lake and sunset views",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_PRIVACY_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Quiet green setting away from tourist zones",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_COMFORT_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Designed for daily family living, not short stays",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_ENGINEERING_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Engineered for comfort: insulation, roof, solar",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8_READY_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "C9 ready in August, with next villas underway",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                    ],
                },
            ],
        },
        "agent_intro_carousel_8_v2": {
            "name": "canopy_agent_intro_carousel_8_v2",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here are 8 concrete Canopy Hills advantages for BISP and "
                        "long-term family buyers in Phuket."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_ESTATE_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Only 9 villas on a private green hillside",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_LAND_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Large plots: 672-1,214 sqm of land",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_GARDEN_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Usable gardens and outdoor family space",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_SCALE_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Real family scale: 650-768 sqm built-up",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_LIVING_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "7m living room ceiling, open family space",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_KITCHEN_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Western & Thai kitchens + 60 sqm BBQ terrace",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_VIEW_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Real views: BISP, lake, hills and sunsets",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL8V2_INSULATION_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Heat & noise insulation 50% above standard",
                                },
                                {"type": "BUTTONS", "buttons": carousel8_buttons},
                            ],
                        },
                    ],
                },
            ],
        },
        "agent_intro_carousel_10_v3": {
            "name": "canopy_agent_intro_carousel_10_v3",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here is a compact Canopy Hills agent pack: key advantages, "
                        "villa formats and Sales Kit link for BISP and long-term family buyers."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 villas on a private green hillside",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Usable large plots: 672-1,214 sqm",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Real family scale: 650-768 sqm built-up",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling, open family space",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Western & Thai kitchens + 60 sqm BBQ terrace",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Green district, away from tourist bustle",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Real views: BISP, lake, hills and sunsets",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat & noise insulation 50% above standard",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, 722 sqm built-up",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm built-up",
                        ),
                    ],
                },
            ],
        },
        "agent_intro_carousel_10_v4": {
            "name": "canopy_agent_intro_carousel_10_v4",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here is a compact Canopy Hills agent pack: key advantages, "
                        "villa formats and Sales Kit link for BISP and long-term family buyers."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 villas on a private green hillside",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Usable large plots: 672-1,214 sqm",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Real family scale: 650-768 sqm built-up",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling, open family space",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Western & Thai kitchens + 60 sqm BBQ terrace",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Green district, away from tourist bustle",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Real views: BISP, lake, hills and sunsets",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat & noise insulation 50% above standard",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, 655 sqm built-up",
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm built-up",
                        ),
                    ],
                },
            ],
        },
        "agent_intro_carousel_10_v5": {
            "name": "canopy_agent_intro_carousel_10_v5",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here is a compact Canopy Hills visual pack: key advantages "
                        "and villa formats for BISP and long-term family buyers."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 villas on a private green hillside",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Usable large plots: 672-1,214 sqm",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Real family scale: 650-768 sqm built-up",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling, open family space",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Western & Thai kitchens + 60 sqm BBQ terrace",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Green district, away from tourist bustle",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Real views: BISP, lake, hills and sunsets",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat & noise insulation 50% above standard",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, 655 sqm built-up",
                            include_buttons=False,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm built-up",
                            include_buttons=False,
                        ),
                    ],
                },
            ],
        },
        "agent_intro_carousel_10_v6": {
            "name": "canopy_agent_intro_carousel_10_v6",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here is a compact Canopy Hills visual pack: key advantages "
                        "and villa formats for BISP and long-term family buyers."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 villas on a private green hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Usable large plots: 672-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Real family scale: 650-768 sqm built-up",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling, open family space",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Western & Thai kitchens + 60 sqm BBQ terrace",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Green district, away from tourist bustle",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Real views: BISP, lake, hills and sunsets",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat & noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, 655 sqm built-up",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm built-up",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v1_en": {
            "name": "canopy_agent_advantages_carousel_10_v1",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Key Canopy Hills advantages for agents and relevant family buyers:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 villas on a private hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Land plots: approx. 670-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Built-up area: approx. 650-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Thai + Western kitchens, 60 sqm BBQ terrace",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Quiet Ko Kaeo location, away from tourist zones",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Views: BISP, lake, hills and sunset",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat and noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, approx. 655 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v1_ru": {
            "name": "canopy_agent_advantages_carousel_10_v1",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Ключевые преимущества Canopy Hills для агентов и семейных покупателей:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Только 9 вилл на приватном холме",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Участки: примерно 670-1,214 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Площадь домов: примерно 650-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "Гостиная с потолком 7 м",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_KITCHEN_HANDLE__",
                            "Thai + Western kitchens, BBQ terrace 60 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Тихая локация Ko Kaeo, не туристическая зона",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Виды: BISP, озеро, холмы и закат",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Тепло- и шумоизоляция на 50% выше стандарта",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 спальни, примерно 655 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 спален, 742-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v2_en": {
            "name": "canopy_agent_advantages_carousel_10_v2",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Key Canopy Hills advantages for agents and relevant buyers:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 view villas on a hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Land plots: approx. 670-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Built-up area: approx. 650-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Investment appeal: school-family rental demand + unique views",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Quiet Ko Kaeo location, away from tourist zones",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Open views: valley, lakes, hills and sunset",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat and noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 bedrooms, approx. 655 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 bedrooms, 742-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v2_ru": {
            "name": "canopy_agent_advantages_carousel_10_v2",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Ключевые преимущества Canopy Hills для агентов и покупателей:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Только 9 видовых вилл на холме",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Участки: примерно 670-1,214 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Площадь домов: примерно 650-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "Гостиная с потолком 7 м",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Инвест. потенциал: аренда семьям у школ + редкий видовой продукт",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Тихая локация Ko Kaeo, не туристическая зона",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Открытые виды: долина, озера, холмы и закат",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Тепло- и шумоизоляция на 50% выше стандарта",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L: 4+1 спальни, примерно 655 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL: 5+1 спален, 742-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v3_en": {
            "name": "canopy_agent_advantages_carousel_10_v3",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Key Canopy Hills advantages for agents and relevant buyers:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 view villas on a hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Land plots: approx. 670-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Built-up area: approx. 650-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Investment appeal: school-family rental demand + unique views",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Quiet Ko Kaeo location, away from tourist zones",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Open views: valley, lakes, hills and sunset",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat and noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "C4 Villa L: 4+1 bedrooms, approx. 606 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "C2 Villa XL: 5+1 bedrooms, approx. 743 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v3_ru": {
            "name": "canopy_agent_advantages_carousel_10_v3",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Ключевые преимущества Canopy Hills для агентов и покупателей:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Только 9 видовых вилл на холме",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Участки: примерно 670-1,214 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Площадь домов: примерно 650-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "Гостиная с потолком 7 м",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Инвест. потенциал: аренда семьям у школ + редкий видовой продукт",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Тихая локация Ko Kaeo, не туристическая зона",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Открытые виды: долина, озера, холмы и закат",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Тепло- и шумоизоляция на 50% выше стандарта",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "C4 Villa L: 4+1 спальни, примерно 606 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "C2 Villa XL: 5+1 спален, примерно 743 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v4_en": {
            "name": "canopy_agent_advantages_carousel_10_v4",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Key Canopy Hills advantages for agents and relevant buyers:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 view villas on a hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Open views: valley, lakes, hills and sunset",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Land plots: 670-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Built-up area: approx. 650-768 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Sustained demand for long-term rentals",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Quiet Ko Kaeo location, away from tourist zones",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat and noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "C4 Villa L: 4+1 bedrooms, approx. 606 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "C2 Villa XL: 5+1 bedrooms, approx. 743 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v4_ru": {
            "name": "canopy_agent_advantages_carousel_10_v4",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Ключевые преимущества Canopy Hills для агентов и покупателей:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Только 9 видовых вилл на холме",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Открытые виды: долина, озера, холмы и закат",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Участки: 670-1,214 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "Площадь домов: примерно 650-768 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "Гостиная с потолком 7 м",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Устойчивый спрос на долгосрочную аренду",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Тихая локация Ko Kaeo, не туристическая зона",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Тепло- и шумоизоляция на 50% выше стандарта",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "C4 Villa L: 4+1 спальни, примерно 606 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "C2 Villa XL: 5+1 спален, примерно 743 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v5_en": {
            "name": "canopy_agent_advantages_carousel_10_v5",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Key Canopy Hills advantages for agents and relevant buyers:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Only 9 view villas on a hillside",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Open views: valley, lakes, hills and sunset",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Land plots: 670-1,214 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "L-size: 650 sqm / XL-size: 750 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "7m living room ceiling",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Sustained demand for long-term rentals",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Quiet Ko Kaeo location, away from tourist zones",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Heat and noise insulation 50% above standard",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Villa L-size layout: 650 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Villa XL-size layout: 750 sqm",
                            buttons=carousel_minimal_buttons,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v5_ru": {
            "name": "canopy_agent_advantages_carousel_10_v5",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": "Ключевые преимущества Canopy Hills для агентов и покупателей:",
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card(
                            "__CAROUSEL10V3_ESTATE_HANDLE__",
                            "Только 9 видовых вилл на холме",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_VIEW_HANDLE__",
                            "Открытые виды: долина, озера, холмы и закат",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_PLOTS_HANDLE__",
                            "Участки: 670-1,214 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_SCALE_HANDLE__",
                            "L-size: 650 м² / XL-size: 750 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_LIVING_HANDLE__",
                            "Гостиная с потолком 7 м",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INVEST_HANDLE__",
                            "Устойчивый спрос на долгосрочную аренду",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_GREEN_HANDLE__",
                            "Тихая локация Ko Kaeo, не туристическая зона",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_INSULATION_HANDLE__",
                            "Тепло- и шумоизоляция на 50% выше стандарта",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_L_LAYOUT_HANDLE__",
                            "Планировка L-size: 650 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                        carousel_image_card(
                            "__CAROUSEL10V3_XL_LAYOUT_HANDLE__",
                            "Планировка XL-size: 750 м²",
                            buttons=carousel_minimal_buttons_ru,
                        ),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v6_en": {
            "name": "canopy_agent_advantages_carousel_10_v6",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {"type": "BODY", "text": "Key Canopy Hills advantages:"},
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card("__CAROUSEL10V3_ESTATE_HANDLE__", "Only 9 view villas on a hillside", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_VIEW_HANDLE__", "Open views: valley, lakes, hills and sunset", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_PLOTS_HANDLE__", "Land plots: 670-1,214 sqm", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_SCALE_HANDLE__", "L-size: 650 sqm / XL-size: 750 sqm", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_LIVING_HANDLE__", "7m living room ceiling", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_INVEST_HANDLE__", "Sustained demand for long-term rentals", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_GREEN_HANDLE__", "Quiet Ko Kaeo location, away from tourist zones", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_INSULATION_HANDLE__", "Heat and noise insulation 50% above standard", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_L_LAYOUT_HANDLE__", "Villa L-size layout: 650 sqm", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_XL_LAYOUT_HANDLE__", "Villa XL-size layout: 750 sqm", buttons=carousel_saleskit_buttons),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v6_ru": {
            "name": "canopy_agent_advantages_carousel_10_v6",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {"type": "BODY", "text": "Ключевые преимущества Canopy Hills:"},
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card("__CAROUSEL10V3_ESTATE_HANDLE__", "Только 9 видовых вилл на холме", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_VIEW_HANDLE__", "Открытые виды: долина, озера, холмы и закат", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_PLOTS_HANDLE__", "Участки: 670-1,214 м²", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_SCALE_HANDLE__", "L-size: 650 м² / XL-size: 750 м²", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_LIVING_HANDLE__", "Гостиная с потолком 7 м", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_INVEST_HANDLE__", "Устойчивый спрос на долгосрочную аренду", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_GREEN_HANDLE__", "Тихая локация Ko Kaeo, не туристическая зона", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_INSULATION_HANDLE__", "Тепло- и шумоизоляция на 50% выше стандарта", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_L_LAYOUT_HANDLE__", "Планировка L-size: 650 м²", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_XL_LAYOUT_HANDLE__", "Планировка XL-size: 750 м²", buttons=carousel_saleskit_buttons_ru),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v7_en": {
            "name": "canopy_agent_advantages_carousel_10_v7",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {"type": "BODY", "text": "Key Canopy Hills advantages:"},
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card("__CAROUSEL10V3_ESTATE_HANDLE__", "Only 9 view villas on a hillside", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_VIEW_HANDLE__", "Open views: valley, lakes, hills and sunset", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_PLOTS_HANDLE__", "Land plots: 670-1,214 sqm", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_SCALE_HANDLE__", "L-size: 650 sqm / XL-size: 750 sqm", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_LIVING_HANDLE__", "7m living room ceiling", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_INVEST_HANDLE__", "Sustained demand for long-term rentals", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_GREEN_HANDLE__", "Quiet Ko Kaeo location, away from tourist zones", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_INSULATION_HANDLE__", "Heat and noise insulation 50% above standard", buttons=carousel_saleskit_buttons),
                        carousel_image_card("__CAROUSEL10V3_L_LAYOUT_HANDLE__", "Villa L-size layout: 650 sqm", buttons=carousel_l_layout_buttons),
                        carousel_image_card("__CAROUSEL10V3_XL_LAYOUT_HANDLE__", "Villa XL-size layout: 750 sqm", buttons=carousel_xl_layout_buttons),
                    ],
                },
            ],
        },
        "agent_advantages_carousel_10_v7_ru": {
            "name": "canopy_agent_advantages_carousel_10_v7",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {"type": "BODY", "text": "Ключевые преимущества Canopy Hills:"},
                {
                    "type": "CAROUSEL",
                    "cards": [
                        carousel_image_card("__CAROUSEL10V3_ESTATE_HANDLE__", "Только 9 видовых вилл на холме", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_VIEW_HANDLE__", "Открытые виды: долина, озера, холмы и закат", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_PLOTS_HANDLE__", "Участки: 670-1,214 м²", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_SCALE_HANDLE__", "L-size: 650 м² / XL-size: 750 м²", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_LIVING_HANDLE__", "Гостиная с потолком 7 м", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_INVEST_HANDLE__", "Устойчивый спрос на долгосрочную аренду", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_GREEN_HANDLE__", "Тихая локация Ko Kaeo, не туристическая зона", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_INSULATION_HANDLE__", "Тепло- и шумоизоляция на 50% выше стандарта", buttons=carousel_saleskit_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_L_LAYOUT_HANDLE__", "Планировка L-size: 650 м²", buttons=carousel_l_layout_buttons_ru),
                        carousel_image_card("__CAROUSEL10V3_XL_LAYOUT_HANDLE__", "Планировка XL-size: 750 м²", buttons=carousel_xl_layout_buttons_ru),
                    ],
                },
            ],
        },
        "agent_intro_carousel_test": {
            "name": "canopy_agent_intro_carousel_test",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, here is a quick Canopy Hills broker preview: a hillside family villa "
                        "estate opposite British International School Phuket, with C9 expected to be ready "
                        "for private viewings in early/mid August."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "CAROUSEL",
                    "cards": [
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL_OVERVIEW_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Club estate of 9 premium hillside villas in Ko Kaeo, opposite BISP.",
                                },
                                {
                                    "type": "BUTTONS",
                                    "buttons": [
                                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                                        {"type": "QUICK_REPLY", "text": "Ask for details"},
                                    ],
                                },
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL_LIVING_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "Spacious 4+1 and 5+1 bedroom villas for long-term family living.",
                                },
                                {
                                    "type": "BUTTONS",
                                    "buttons": [
                                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                                        {"type": "QUICK_REPLY", "text": "Register client"},
                                    ],
                                },
                            ],
                        },
                        {
                            "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {"header_handle": ["__CAROUSEL_VIEW_HANDLE__"]},
                                },
                                {
                                    "type": "BODY",
                                    "text": "C9 is expected to be ready for private viewings in early/mid August.",
                                },
                                {
                                    "type": "BUTTONS",
                                    "buttons": [
                                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                                        {"type": "QUICK_REPLY", "text": "Arrange viewing"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
        "agent_saleskit_intro_image": {
            "name": "canopy_agent_saleskit_intro_image",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "HEADER",
                    "format": "IMAGE",
                    "example": {
                        "header_handle": ["__META_HEADER_HANDLE__"]
                    },
                },
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, sharing a quick broker pack for Canopy Hills Villas Phuket.\n\n"
                        "Canopy Hills is a club estate of 9 premium hillside villas in Ko Kaeo, "
                        "opposite British International School Phuket.\n\n"
                        "Designed for families relocating to Phuket or living on the island full-time: "
                        "spacious 4+1 and 5+1 bedroom villas, approx. 650-768 sqm built-up, panoramic views, "
                        "privacy, quiet green surroundings and daily infrastructure nearby.\n\n"
                        "Current availability to discuss: C1, C2, C3 and C6. Villa C9 is nearly ready, "
                        "with private viewings expected in early/mid August. C7-C8 construction has started.\n\n"
                        "Price range: from approx. THB 57.5M. Agent commission: 6%.\n\n"
                        "If you have a client focused on BISP, long-term family living and a near-ready "
                        "premium villa, we can pre-arrange a private C9 viewing for early/mid August."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                        {"type": "QUICK_REPLY", "text": "Register client"},
                        {"type": "QUICK_REPLY", "text": "Arrange viewing"},
                    ],
                },
            ],
        },
        "agent_saleskit_intro": {
            "name": "canopy_agent_saleskit_intro",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, sharing a quick broker pack for Canopy Hills Villas Phuket.\n\n"
                        "Canopy Hills is a club estate of 9 premium hillside villas in Ko Kaeo, "
                        "opposite British International School Phuket.\n\n"
                        "Designed for families relocating to Phuket or living on the island full-time: "
                        "spacious 4+1 and 5+1 bedroom villas, approx. 650-768 sqm built-up, panoramic views, "
                        "privacy, quiet green surroundings and daily infrastructure nearby.\n\n"
                        "Current availability to discuss: C1, C2, C3 and C6. Villa C9 is nearly ready, "
                        "with private viewings expected in early/mid August. C7-C8 construction has started.\n\n"
                        "Price range: from approx. THB 57.5M. Agent commission: 6%.\n\n"
                        "If you have a client focused on BISP, long-term family living and a near-ready "
                        "premium villa, we can pre-arrange a private C9 viewing for early/mid August."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "URL", "text": "Open Sales Kit", "url": sales_kit_url},
                        {"type": "QUICK_REPLY", "text": "Register client"},
                        {"type": "QUICK_REPLY", "text": "Arrange viewing"},
                    ],
                },
            ],
        },
        "ready_villa_update": {
            "name": "canopy_ready_villa_update",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, quick Canopy Hills update: our first villa C9 is expected "
                        "to be ready for private viewings in early/mid August, and construction "
                        "of the next villas has already started.\n\n"
                        "This means the project can now be reviewed beyond renders and the show unit: "
                        "a real villa plus visible construction progress.\n\n"
                        "If a ready or near-ready villa is still relevant for you or your client, "
                        "we can add you to the priority private preview list."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Private viewing"},
                        {"type": "QUICK_REPLY", "text": "Send availability"},
                    ],
                },
            ],
        },
        "new_lead_qualification": {
            "name": "canopy_new_lead_qualification",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, thank you for your interest in Canopy Hills Villas.\n\n"
                        "Canopy Hills is a club estate of 9 premium hillside villas opposite "
                        "British International School Phuket, designed for long-term family living "
                        "rather than short holiday stays.\n\n"
                        "May I ask if you are looking for a villa for yourself/family, or representing "
                        "a client? Then I can send the most relevant availability, pricing and viewing details."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "For myself"},
                        {"type": "QUICK_REPLY", "text": "For my client"},
                    ],
                },
            ],
        },
        "broker_preview_august_ru": {
            "name": "canopy_broker_preview_august_ru",
            "language": "ru",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Добрый день, {{1}}! Короткое обновление по Canopy Hills: первая вилла C9 "
                        "будет готова к приватным просмотрам в начале/середине августа.\n\n"
                        "Canopy Hills - клубный поселок из 9 премиальных вилл на холме в Ko Kaeo, "
                        "напротив British International School Phuket. Проект создан для долгосрочной "
                        "семейной жизни: просторные виллы, панорамные виды, приватность, тихая зеленая "
                        "локация и ежедневная инфраструктура рядом.\n\n"
                        "Комиссия для агентов - 6%. Если у вас есть клиенты, которым важны BISP, "
                        "семейная локация и почти готовый продукт, можем заранее согласовать private preview."
                    ),
                    "example": {"body_text": [["коллеги"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Получить детали"},
                        {"type": "QUICK_REPLY", "text": "Согласовать просмотр"},
                    ],
                },
            ],
        },
        "c9_private_preview_invite": {
            "name": "canopy_c9_private_preview_invite",
            "language": "en_US",
            "category": "MARKETING",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Hi {{1}}, we are preparing private preview slots for villa C9 at "
                        "Canopy Hills in early/mid August.\n\n"
                        "This preview is meant for serious buyers and agents with relevant clients "
                        "who want to see a real near-ready villa, not only renders.\n\n"
                        "If you would like to arrange a private viewing, please reply with preferred "
                        "dates and whether the visit is for yourself or for a client."
                    ),
                    "example": {"body_text": [["there"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Request slot"},
                        {"type": "QUICK_REPLY", "text": "Client visit"},
                    ],
                },
            ],
        },
        "vladimir_need_reply": {
            "name": "codex_need_vladimir_reply_ru",
            "language": "ru",
            "category": "UTILITY",
            "components": [
                {
                    "type": "BODY",
                    "text": (
                        "Володя, мне нужен твой короткий ответ по Canopy, чтобы двигаться дальше. "
                        "Пожалуйста, ответь в этот WhatsApp-чат."
                    ),
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Отвечу сейчас"},
                        {"type": "QUICK_REPLY", "text": "Позже"},
                    ],
                },
            ],
        },
    }
    return templates.get(template_key)


def create_canopy_template(template_key):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    waba_id = os.environ.get("WHATSAPP_WABA_ID", DEFAULT_WABA_ID).strip()
    app_id = os.environ.get("WHATSAPP_APP_ID", DEFAULT_APP_ID).strip()
    payload = canopy_template_payload(template_key)
    result = {
        "ok": False,
        "graph_version": graph_version,
        "waba_id": waba_id,
        "app_id": app_id,
        "template_key": template_key,
        "payload_name": payload.get("name") if payload else "",
    }
    if not payload:
        result["error"] = "unknown template_key"
        return result
    if not access_token or not waba_id:
        result["error"] = "WHATSAPP_ACCESS_TOKEN or WHATSAPP_WABA_ID is not set"
        return result
    if template_key == "agent_saleskit_intro_image":
        sample_path = ASSET_DIR / "agent_header.jpg"
        upload = safe_graph_upload_file_handle(access_token, graph_version, app_id, sample_path)
        result["sample_upload"] = upload
        if not upload.get("ok"):
            result["error"] = "failed to upload image sample to Meta"
            return result
        header_handle = upload["data"]["handle"]
        for component in payload.get("components", []):
            if component.get("type") == "HEADER" and component.get("format") == "IMAGE":
                component["example"] = {"header_handle": [header_handle]}

    if template_key == "agent_intro_carousel_test":
        carousel_samples = [
            ("__CAROUSEL_OVERVIEW_HANDLE__", ASSET_DIR / "carousel_overview.jpg"),
            ("__CAROUSEL_LIVING_HANDLE__", ASSET_DIR / "carousel_living.jpg"),
            ("__CAROUSEL_VIEW_HANDLE__", ASSET_DIR / "carousel_view.jpg"),
        ]
        result["sample_uploads"] = []
        handles = {}
        for placeholder, sample_path in carousel_samples:
            upload = safe_graph_upload_file_handle(access_token, graph_version, app_id, sample_path)
            result["sample_uploads"].append({"placeholder": placeholder, "upload": upload})
            if not upload.get("ok"):
                result["error"] = f"failed to upload carousel sample {sample_path.name} to Meta"
                return result
            handles[placeholder] = upload["data"]["handle"]

        for component in payload.get("components", []):
            if component.get("type") == "CAROUSEL":
                for card in component.get("cards", []):
                    for card_component in card.get("components", []):
                        example = card_component.get("example", {})
                        header_handle = example.get("header_handle", [])
                        if header_handle and header_handle[0] in handles:
                            card_component["example"] = {"header_handle": [handles[header_handle[0]]]}

    if template_key == "agent_intro_carousel_8_v1":
        carousel_samples = [
            ("__CAROUSEL8_ESTATE_HANDLE__", ASSET_DIR / "carousel8_01_estate.jpg"),
            ("__CAROUSEL8_BISP_HANDLE__", ASSET_DIR / "carousel8_02_bisp_family.jpg"),
            ("__CAROUSEL8_SPACIOUS_HANDLE__", ASSET_DIR / "carousel8_03_spacious.jpg"),
            ("__CAROUSEL8_VIEWS_HANDLE__", ASSET_DIR / "carousel8_04_views.jpg"),
            ("__CAROUSEL8_PRIVACY_HANDLE__", ASSET_DIR / "carousel8_05_private_bedroom.jpg"),
            ("__CAROUSEL8_COMFORT_HANDLE__", ASSET_DIR / "carousel8_06_daily_comfort.jpg"),
            ("__CAROUSEL8_ENGINEERING_HANDLE__", ASSET_DIR / "carousel8_07_engineering.jpg"),
            ("__CAROUSEL8_READY_HANDLE__", ASSET_DIR / "carousel8_08_layout.jpg"),
        ]
        result["sample_uploads"] = []
        handles = {}
        for placeholder, sample_path in carousel_samples:
            upload = safe_graph_upload_file_handle(access_token, graph_version, app_id, sample_path)
            result["sample_uploads"].append({"placeholder": placeholder, "upload": upload})
            if not upload.get("ok"):
                result["error"] = f"failed to upload carousel sample {sample_path.name} to Meta"
                return result
            handles[placeholder] = upload["data"]["handle"]

        for component in payload.get("components", []):
            if component.get("type") == "CAROUSEL":
                for card in component.get("cards", []):
                    for card_component in card.get("components", []):
                        example = card_component.get("example", {})
                        header_handle = example.get("header_handle", [])
                        if header_handle and header_handle[0] in handles:
                            card_component["example"] = {"header_handle": [handles[header_handle[0]]]}

    if template_key == "agent_intro_carousel_8_v2":
        carousel_samples = [
            ("__CAROUSEL8V2_ESTATE_HANDLE__", ASSET_DIR / "carousel_v2_01_private_hillside_estate.jpg"),
            ("__CAROUSEL8V2_LAND_HANDLE__", ASSET_DIR / "carousel_v2_02_large_land_plots.jpg"),
            ("__CAROUSEL8V2_GARDEN_HANDLE__", ASSET_DIR / "carousel_v2_03_usable_garden.jpg"),
            ("__CAROUSEL8V2_SCALE_HANDLE__", ASSET_DIR / "carousel_v2_04_family_scale.jpg"),
            ("__CAROUSEL8V2_LIVING_HANDLE__", ASSET_DIR / "carousel_v2_05_7m_living_room.jpg"),
            ("__CAROUSEL8V2_KITCHEN_HANDLE__", ASSET_DIR / "carousel_v2_06_kitchens_bbq.jpg"),
            ("__CAROUSEL8V2_VIEW_HANDLE__", ASSET_DIR / "carousel_v2_07_real_view.jpg"),
            ("__CAROUSEL8V2_INSULATION_HANDLE__", ASSET_DIR / "carousel_v2_08_heat_noise_insulation.jpg"),
        ]
        result["sample_uploads"] = []
        handles = {}
        for placeholder, sample_path in carousel_samples:
            upload = safe_graph_upload_file_handle(access_token, graph_version, app_id, sample_path)
            result["sample_uploads"].append({"placeholder": placeholder, "upload": upload})
            if not upload.get("ok"):
                result["error"] = f"failed to upload carousel sample {sample_path.name} to Meta"
                return result
            handles[placeholder] = upload["data"]["handle"]

        for component in payload.get("components", []):
            if component.get("type") == "CAROUSEL":
                for card in component.get("cards", []):
                    for card_component in card.get("components", []):
                        example = card_component.get("example", {})
                        header_handle = example.get("header_handle", [])
                        if header_handle and header_handle[0] in handles:
                            card_component["example"] = {"header_handle": [handles[header_handle[0]]]}

    if template_key == "agent_video_intro_cta_v1":
        upload = safe_graph_upload_file_handle(
            access_token,
            graph_version,
            app_id,
            ASSET_DIR / "agent_intro_video.mp4",
        )
        result["sample_uploads"] = [{"placeholder": "__AGENT_INTRO_VIDEO_HANDLE__", "upload": upload}]
        if not upload.get("ok"):
            result["error"] = "failed to upload agent intro video sample to Meta"
            return result
        handle = upload["data"]["handle"]
        for component in payload.get("components", []):
            if component.get("type") == "HEADER":
                example = component.get("example", {})
                header_handle = example.get("header_handle", [])
                if header_handle and header_handle[0] == "__AGENT_INTRO_VIDEO_HANDLE__":
                    component["example"] = {"header_handle": [handle]}

    if template_key in (
        "agent_intro_carousel_10_v3",
        "agent_intro_carousel_10_v4",
        "agent_intro_carousel_10_v5",
        "agent_intro_carousel_10_v6",
        "agent_advantages_carousel_10_v1_en",
        "agent_advantages_carousel_10_v1_ru",
        "agent_advantages_carousel_10_v2_en",
        "agent_advantages_carousel_10_v2_ru",
        "agent_advantages_carousel_10_v3_en",
        "agent_advantages_carousel_10_v3_ru",
        "agent_advantages_carousel_10_v4_en",
        "agent_advantages_carousel_10_v4_ru",
        "agent_advantages_carousel_10_v5_en",
        "agent_advantages_carousel_10_v5_ru",
        "agent_advantages_carousel_10_v6_en",
        "agent_advantages_carousel_10_v6_ru",
        "agent_advantages_carousel_10_v7_en",
        "agent_advantages_carousel_10_v7_ru",
    ):
        carousel_samples = [
            ("__CAROUSEL10V3_ESTATE_HANDLE__", ASSET_DIR / "carousel_v3_01_private_hillside_estate.jpg"),
            ("__CAROUSEL10V3_PLOTS_HANDLE__", ASSET_DIR / "carousel_v3_02_usable_large_plots.jpg"),
            ("__CAROUSEL10V3_SCALE_HANDLE__", ASSET_DIR / "carousel_v3_03_real_family_scale.jpg"),
            ("__CAROUSEL10V3_LIVING_HANDLE__", ASSET_DIR / "carousel_v3_04_7m_living_room.jpg"),
            ("__CAROUSEL10V3_KITCHEN_HANDLE__", ASSET_DIR / "carousel_v3_05_kitchens_bbq.jpg"),
            ("__CAROUSEL10V3_INVEST_HANDLE__", ASSET_DIR / "carousel_v3_05_investment_bisp.jpg"),
            ("__CAROUSEL10V3_GREEN_HANDLE__", ASSET_DIR / "carousel_v3_06_green_district.jpg"),
            ("__CAROUSEL10V3_VIEW_HANDLE__", ASSET_DIR / "carousel_v3_07_real_view.jpg"),
            ("__CAROUSEL10V3_INSULATION_HANDLE__", ASSET_DIR / "carousel_v3_08_heat_noise_insulation.jpg"),
            ("__CAROUSEL10V3_L_LAYOUT_HANDLE__", ASSET_DIR / "carousel_v3_09_villa_l_layout.jpg"),
            ("__CAROUSEL10V3_XL_LAYOUT_HANDLE__", ASSET_DIR / "carousel_v3_10_villa_xl_layout.jpg"),
        ]
        result["sample_uploads"] = []
        handles = {}
        for placeholder, sample_path in carousel_samples:
            upload = safe_graph_upload_file_handle(access_token, graph_version, app_id, sample_path)
            result["sample_uploads"].append({"placeholder": placeholder, "upload": upload})
            if not upload.get("ok"):
                result["error"] = f"failed to upload carousel sample {sample_path.name} to Meta"
                return result
            handles[placeholder] = upload["data"]["handle"]

        for component in payload.get("components", []):
            if component.get("type") == "CAROUSEL":
                for card in component.get("cards", []):
                    for card_component in card.get("components", []):
                        example = card_component.get("example", {})
                        header_handle = example.get("header_handle", [])
                        if header_handle and header_handle[0] in handles:
                            card_component["example"] = {"header_handle": [handles[header_handle[0]]]}

    response = safe_graph_post(access_token, graph_version, f"{waba_id}/message_templates", payload)
    result["meta"] = response
    result["ok"] = bool(response.get("ok"))
    return result


def whatsapp_templates(template_name=""):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    waba_id = os.environ.get("WHATSAPP_WABA_ID", DEFAULT_WABA_ID).strip()
    result = {
        "ok": False,
        "graph_version": graph_version,
        "waba_id": waba_id,
        "has_access_token": bool(access_token),
        "template_name": template_name,
        "templates": [],
    }
    if not access_token or not waba_id:
        result["error"] = "WHATSAPP_ACCESS_TOKEN or WHATSAPP_WABA_ID is not set"
        return result

    fields = "name,status,category,language,quality_score,components"
    query = urlencode({"fields": fields, "limit": "100"})
    response = safe_graph_get(access_token, graph_version, f"{waba_id}/message_templates", query)
    result["meta"] = response
    if not response.get("ok"):
        return result

    templates = response.get("data", {}).get("data", [])
    if template_name:
        templates = [item for item in templates if item.get("name") == template_name]
    result["templates"] = templates
    result["ok"] = True
    return result


def whatsapp_diagnostics(waba_id=""):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    result = {
        "ok": False,
        "graph_version": graph_version,
        "phone_number_id": phone_number_id,
        "has_access_token": bool(access_token),
    }
    if not access_token or not phone_number_id:
        result["error"] = "WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is not set"
        return result

    phone_check = safe_graph_get(
        access_token,
        graph_version,
        phone_number_id,
        "fields=id,display_phone_number,verified_name,quality_rating,platform_type",
    )
    result["phone_number_check"] = phone_check

    if waba_id:
        result["waba_id"] = waba_id
        result["waba_phone_numbers"] = safe_graph_get(
            access_token,
            graph_version,
            f"{waba_id}/phone_numbers",
            "fields=id,display_phone_number,verified_name,quality_rating,platform_type",
        )

    result["ok"] = phone_check.get("ok", False)
    return result


def waba_webhook_subscription(action="status", waba_id=""):
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    graph_version = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0").strip()
    target_waba_id = (waba_id or os.environ.get("WHATSAPP_WABA_ID", DEFAULT_WABA_ID)).strip()
    result = {
        "ok": False,
        "action": action,
        "graph_version": graph_version,
        "waba_id": target_waba_id,
        "has_access_token": bool(access_token),
    }
    if not access_token or not target_waba_id:
        result["error"] = "WHATSAPP_ACCESS_TOKEN or WHATSAPP_WABA_ID is not set"
        return result

    if action == "subscribe":
        result["subscribe"] = safe_graph_post(
            access_token,
            graph_version,
            f"{target_waba_id}/subscribed_apps",
            {},
        )

    result["subscribed_apps"] = safe_graph_get(
        access_token,
        graph_version,
        f"{target_waba_id}/subscribed_apps",
        "fields=id,name,link",
    )
    result["phone_numbers"] = safe_graph_get(
        access_token,
        graph_version,
        f"{target_waba_id}/phone_numbers",
        "fields=id,display_phone_number,verified_name,quality_rating,platform_type",
    )
    result["ok"] = bool(result["subscribed_apps"].get("ok"))
    return result


def rows_to_json(rows):
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2).encode("utf-8")


def rows_to_csv(headers, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in rows:
        item = dict(row)
        writer.writerow([item.get(header, "") for header in headers])
    return out.getvalue().encode("utf-8-sig")


def send_csv(handler, body, filename):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", f'inline; filename="{filename}"')
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def is_russian_text(text):
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in (text or ""))


def draft_reply(contact, last_text=""):
    segment = contact.get("segment") or "new_inbound"
    ru = is_russian_text(last_text)
    if segment == "investor":
        if ru:
            return (
                "Добрый день! Спасибо за интерес к Canopy Hills.\n\n"
                "По инвестиционным сценариям лучше коротко созвониться: там важно понять сумму, горизонт и какой формат вам ближе - "
                "участие в строительстве конкретной виллы или покупка готового актива.\n\n"
                "Можем отправить актуальный статус проекта и договориться о звонке. Когда вам удобно сегодня/завтра?"
            )
        return (
            "Hi, thank you for your interest in Canopy Hills.\n\n"
            "For investment scenarios, it is better to have a short call first: we need to understand the amount, horizon, "
            "and whether you are considering construction-stage participation or ownership of a completed asset.\n\n"
            "We can share the current project status and arrange a call. What time works for you today or tomorrow?"
        )
    if segment == "client_registration":
        if ru:
            return (
                "Отлично, давайте зарегистрируем клиента и подготовим следующий шаг.\n\n"
                "Пришлите, пожалуйста: имя клиента, страну/город, желаемую дату просмотра и какой формат он ищет - "
                "готовую/почти готовую виллу или виллу в строительстве. После этого подтвердим регистрацию."
            )
        return (
            "Great, let us register the client and prepare the next step.\n\n"
            "Please send the client name, country/city, preferred viewing date, and whether they are looking for a ready/near-ready villa "
            "or a villa under construction. Then we will confirm the registration."
        )
    if segment == "trust_legal":
        if ru:
            return (
                "Понимаю вопрос. Для серьезного покупателя юридическая и строительная проверка важна не меньше, чем планировка.\n\n"
                "Земельный участок находится в собственности Hugs Management Co., Ltd. Под каждую виллу выделен отдельный титул права собственности, "
                "поэтому структуру сделки можно обсуждать как в формате leasehold, так и freehold - в зависимости от ситуации покупателя и выбранной структуры.\n\n"
                "Для детальной юридической проверки можем организовать отдельное обсуждение с командой проекта и предоставить документы на этапе due diligence.\n\n"
                "У вас уже есть юрист/консультант, который будет смотреть документы?"
            )
        return (
            "I understand the question. For a serious buyer, legal and construction due diligence is as important as the layout.\n\n"
            "The land plot is owned by Hugs Management Co., Ltd. Each villa plot has its own separate land title, "
            "so the transaction structure can be discussed as either leasehold or freehold, depending on the buyer's situation and preferred structure.\n\n"
            "For a detailed legal review, we can arrange a separate discussion with the project team and provide the relevant documents at the due diligence stage.\n\n"
            "Do you already have a lawyer/advisor who will review the documents?"
        )
    if segment == "materials_request":
        if ru:
            return (
                "Добрый день! Спасибо за интерес к Canopy Hills.\n\n"
                "Canopy Hills - клубный поселок из 9 видовых вилл на холме в Ko Kaeo, рядом с BISP, международными школами, "
                "Central Phuket, гольфом, маринами и повседневной инфраструктурой. Проект рассчитан на тех, кто ищет просторный "
                "приватный дом в тихой зеленой локации с открытыми видами на долину, озера и холмы.\n\n"
                "Презентация проекта: https://drive.google.com/file/d/1jlBF9tc1mtX-ygI1kletcuqf9skex58T/view\n\n"
                "Чтобы дать вам наиболее релевантную информацию, подскажите, пожалуйста: вы рассматриваете виллу в первую очередь "
                "для семейной жизни, lifestyle/переезда или как инвестицию?"
            )
        return (
            "Hi, thank you for your interest in Canopy Hills.\n\n"
            "Canopy Hills is a club-style estate of 9 hillside view villas in Ko Kaeo, close to BISP, international schools, "
            "Central Phuket, golf, marinas and everyday infrastructure. The project is designed for people who want a spacious "
            "private home in a quiet green location, with open views over the valley, lakes and hills.\n\n"
            "Project presentation: https://drive.google.com/file/d/1c1djBre5fRbmeoLXPsLYAczRFFIXbUvL/view\n\n"
            "To send you the most relevant details, may I ask if you are considering a villa mainly for family living, "
            "lifestyle/relocation, or investment?"
        )
    if segment == "quality_engineering":
        if ru:
            return (
                "Качество для нас - не только отделка. В Canopy Hills оно связано с тем, как дом будет жить каждый день: "
                "пространство, вид, термо- и шумоизоляция, инженерия, хранение, крыша, окна, материалы и удобство для семьи.\n\n"
                "Лучше всего это видно на месте: в шоу-юнитe, на строящейся вилле и затем на первой готовой вилле C9. "
                "Могу отправить краткий engineering/materials pack или предложить приватный просмотр."
            )
        return (
            "For us, quality is not only about finishes. At Canopy Hills it is about how the house works for daily life: "
            "space, views, thermal and sound insulation, engineering, storage, roof, windows, materials and family comfort.\n\n"
            "The best proof is on site: the show unit, the villa under construction, and then the first completed villa C9. "
            "I can share a short engineering/materials pack or arrange a private viewing."
        )
    if segment == "viewing_request":
        if ru:
            return (
                "Добрый день! Да, можем организовать приватный показ.\n\n"
                "Подскажите, пожалуйста, вы рассматриваете виллу для себя/семьи или представляете клиента? "
                "И какой день вам удобнее для просмотра?"
            )
        return (
            "Hi, yes, we can arrange a private viewing.\n\n"
            "May I ask if you are considering a villa for yourself/family, or representing a client? "
            "And which day would be convenient for you to visit?"
        )
    if segment == "ready_villa_buyer":
        if ru:
            return (
                "Добрый день! Спасибо за обращение.\n\n"
                "Первая вилла уже близка к готовности, и мы можем показать её приватно. "
                "Также сейчас начато строительство следующих вилл, поэтому проект уже можно смотреть не только по рендерам.\n\n"
                "Вы рассматриваете покупку для себя/семьи или для клиента?"
            )
        return (
            "Hi, thank you for reaching out.\n\n"
            "Our first villa is close to completion and can be shown privately. Construction of the next villas has also started, "
            "so the project can now be reviewed beyond renders.\n\n"
            "Are you considering a villa for yourself/family, or for a client?"
        )
    if segment == "broker":
        if ru:
            return (
                "Добрый день! Спасибо за сообщение.\n\n"
                "Отправляю агентский пакет по Canopy Hills: короткое видео и визуальный обзор ключевых преимуществ проекта. "
                "Стандартная комиссия для агентов - 6%.\n\n"
                "Если есть клиент под проект, следующим шагом пришлите данные для регистрации клиента и желаемую дату просмотра."
            )
        return (
            "Hi, thank you for contacting Canopy Hills.\n\n"
            "Sharing the Canopy Hills agent package: a short intro video and a visual overview of the key advantages. "
            "The standard agent commission is 6%.\n\n"
            "If you have a client for the project, please send the client registration details and preferred viewing date as the next step."
        )
    if segment == "family_bisp_buyer":
        if ru:
            return (
                "Добрый день! Спасибо за интерес.\n\n"
                "Canopy Hills как раз рассчитан на долгосрочную семейную жизнь рядом с British International School: "
                "просторные виллы, вид, тишина, приватность и вся повседневная инфраструктура рядом.\n\n"
                "Подскажите, вы уже живёте на Пхукете или планируете переезд к новому учебному году?"
            )
        return (
            "Hi, thank you for your interest.\n\n"
            "Canopy Hills is designed for long-term family living near British International School: spacious villas, views, privacy, "
            "quiet surroundings, and everyday infrastructure nearby.\n\n"
            "Are you already living in Phuket, or planning to relocate for the new school year?"
        )
    if segment == "price_payment":
        if ru:
            return (
                "Добрый день! Можем отправить актуальную доступность и цены.\n\n"
                "Чтобы дать релевантную информацию, подскажите, пожалуйста: вы рассматриваете виллу для себя или представляете клиента? "
                "И вам важнее готовая/почти готовая вилла или можно рассматривать виллы в строительстве?"
            )
        return (
            "Hi, we can share current availability and pricing.\n\n"
            "To send the most relevant information, may I ask if you are considering a villa for yourself or representing a client? "
            "And are you looking for a ready/near-ready villa, or also considering villas under construction?"
        )
    if segment == "low_relevance":
        if ru:
            return (
                "Добрый день. Спасибо за сообщение.\n\n"
                "Если ваш запрос связан с покупкой виллы в Canopy Hills, пожалуйста, напишите, что именно вы рассматриваете, "
                "и мы вернёмся с актуальной информацией."
            )
        return (
            "Hi, thank you for your message.\n\n"
            "If your request is related to purchasing a villa at Canopy Hills, please let us know what you are considering, "
            "and we will share the relevant information."
        )
    if ru:
        return (
            "Добрый день! Спасибо за интерес к Canopy Hills.\n\n"
            "Подскажите, пожалуйста, вы рассматриваете виллу для себя/семьи или представляете клиента? "
            "После этого я отправлю наиболее релевантную информацию по доступности, цене и просмотру."
        )
    return (
        "Hi, thank you for contacting Canopy Hills.\n\n"
        "May I ask if you are looking for a villa for yourself/family, or representing a client? "
        "Then I can send the most relevant availability, pricing, and viewing details."
    )


def suggested_materials(segment):
    materials = {
        "client_registration": [
            "Client registration note: full name, country/city, agent name, preferred viewing date.",
            "Location pin after viewing is confirmed: https://maps.app.goo.gl/imEAqyCVY6d15wmy9?g_st=ipc",
            "Relevant unit one-pager after villa preference is known.",
        ],
        "trust_legal": [
            "Legal/DD pack only after qualification: Hugs Management structure, land/title/permit docs, draft agreements.",
            "Sample C9 agreements if appropriate: Land Lease Agreement and Villa Sale and Purchase Agreement.",
            "Escalate to Vladimir/Andrey before sending sensitive documents.",
        ],
        "quality_engineering": [
            "Engineering and Sustainability pack.",
            "Interiors and Finishes pack.",
            "Construction/show unit/C9 proof photos or technical viewing.",
        ],
        "materials_request": [
            "Default agent welcome pack: intro video with caption + v7 advantages carousel. Layout cards link to grouped C1-C9 layout pages; do not send separate layout PDF documents by default.",
            "Do not ask whether they have a specific client or want materials for database before sending the pack.",
            "After the pack, ask for client registration/viewing details only if they respond with a concrete client or visit request.",
            "Clients: relevant RU/EN/CH presentation, not full SalesKit.",
        ],
        "investor": [
            "No detailed investor offer before qualification.",
            "After call: C1 investor note / current project status / DD documents.",
            "Escalate to Vladimir/Andrey.",
        ],
        "viewing_request": [
            "Location pin after time is confirmed: https://maps.app.goo.gl/imEAqyCVY6d15wmy9?g_st=ipc",
            "Access instruction: enter through the soi next to The Big Bear Kitchen.",
            "Current availability if buyer asks what to view.",
        ],
        "ready_villa_buyer": [
            "C9 progress media / private preview invite.",
            "Current availability and PRICE May 2026.",
            "Project status one-pager: C9 near-ready, C7/C8 started, C6/C1-C3 availability logic.",
        ],
        "broker": [
            "SalesKit: https://drive.google.com/drive/folders/1oSpCppxgLdRXUrHyxn8tFftyPLB4PiP5",
            "PRICE May 2026.",
            "Commission 6% and client registration rules.",
        ],
        "family_bisp_buyer": [
            "ENG/RUS presentation depending on language.",
            "Location/surroundings and BISP-family positioning.",
            "Private viewing or C9 preview invite.",
        ],
        "price_payment": [
            "PRICE May 2026.",
            "Specific villa one-pager after C6/C1/C2/C3/C9 preference.",
            "Payment schedule if requested.",
        ],
        "low_relevance": ["No materials."],
        "new_inbound": ["No materials before buyer/agent qualification unless explicitly requested."],
    }
    return materials.get(segment, materials["new_inbound"])


def render_materials(segment):
    items = "".join(f"<li>{escape(item)}</li>" for item in suggested_materials(segment))
    return f"<ul>{items}</ul>"


def render_playbook():
    body = """
    <section class="panel">
      <h2>First response rule</h2>
      <p>Do not oversell in the first message. Acknowledge, classify the lead, ask one qualifying question, and offer one clear next step.</p>
    </section>
    <section class="panel">
      <h2>Segments</h2>
      <table>
        <thead><tr><th>Segment</th><th>Priority</th><th>Goal</th><th>Next step</th></tr></thead>
        <tbody>
          <tr><td>viewing_request</td><td>P1</td><td>Convert to appointment</td><td>Offer two viewing slots; confirm buyer or agent</td></tr>
          <tr><td>ready_villa_buyer</td><td>P1</td><td>Move from curiosity to private preview</td><td>Explain C9 readiness and active construction</td></tr>
          <tr><td>family_bisp_buyer</td><td>P1</td><td>Anchor BISP / long-term living positioning</td><td>Ask relocation timing and offer preview</td></tr>
          <tr><td>investor</td><td>P1</td><td>Escalate to principal conversation</td><td>Offer call before sending detailed terms</td></tr>
          <tr><td>client_registration</td><td>P1</td><td>Protect broker/client attribution</td><td>Collect client name, origin, timing, villa preference</td></tr>
          <tr><td>trust_legal</td><td>P2</td><td>Handle due diligence calmly</td><td>Qualify seriousness before legal pack</td></tr>
          <tr><td>materials_request</td><td>P2</td><td>Send forwardable intro package</td><td>Welcome capsule + 4 renders + SalesKit, then qualify</td></tr>
          <tr><td>quality_engineering</td><td>P2</td><td>Prove premium construction quality</td><td>Offer engineering pack or technical viewing</td></tr>
          <tr><td>broker</td><td>P2</td><td>Activate agent channel</td><td>Send agent pack, 6% commission, client registration</td></tr>
          <tr><td>price_payment</td><td>P2</td><td>Clarify product fit</td><td>Ask ready vs under-construction and buyer vs client</td></tr>
          <tr><td>new_inbound</td><td>P3</td><td>Qualify role</td><td>Ask buyer/family or agent/client</td></tr>
          <tr><td>low_relevance</td><td>P4</td><td>Protect sales time</td><td>Only answer if they clarify villa-related request</td></tr>
        </tbody>
      </table>
    </section>
    """
    return page("Canopy Lead Response Playbook", body)


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
    .p4 {{ color: var(--muted); font-weight: 650; }}
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
        '<div><a href="/playbook">Response playbook</a> · <a href="/leads">JSON leads</a> · <a href="/events">Webhook events</a></div>',
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
    last_text = dict(messages[-1]).get("text", "") if messages else ""
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
        f"<pre>{escape(draft_reply(contact_dict, last_text))}</pre>",
        "</section>",
        '<section class="panel">',
        "<h2>Suggested materials</h2>",
        render_materials(contact_dict.get("segment") or "new_inbound"),
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


def operator_feed(limit=20):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))

    con = db()
    rows = con.execute(
        """
        SELECT
            m.id,
            m.wa_id,
            COALESCE(c.profile_name, '') AS profile_name,
            m.message_type,
            m.text,
            m.received_at,
            COALESCE(c.segment, 'new_inbound') AS segment,
            COALESCE(c.priority, 'P3') AS priority,
            COALESCE(c.next_action, '') AS next_action
        FROM messages m
        LEFT JOIN contacts c ON c.wa_id = m.wa_id
        WHERE m.direction = 'inbound'
        ORDER BY m.received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in reversed(rows):
        if row["wa_id"] not in AI_OPERATOR_WA_IDS or row["message_type"] != "text":
            continue
        command = normalize_mode_command(row["text"])
        if command in {"тест", "test"}:
            set_operator_mode(con, row["wa_id"], "lead_test")
        elif command in {
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
        }:
            set_operator_mode(con, row["wa_id"], "work")
    con.commit()

    items = []
    for row in rows:
        contact = dict(row)
        operator_mode = get_operator_mode(con, row["wa_id"])
        is_operator = row["wa_id"] in AI_OPERATOR_WA_IDS
        items.append(
            {
                "message_id": row["id"],
                "wa_id": row["wa_id"],
                "profile_name": row["profile_name"],
                "message_type": row["message_type"],
                "text": row["text"],
                "received_at": row["received_at"],
                "segment": row["segment"],
                "priority": row["priority"],
                "next_action": None,
                "is_operator": is_operator,
                "operator_mode": operator_mode,
                "operator_test_mode": bool(is_operator and operator_mode == "lead_test"),
                "suggested_reply": None,
                "suggested_materials": [],
            }
        )
    con.close()
    return items


def developer_research_followup_draft(target, last_text=""):
    developer = target.get("developer") or "team"
    next_question = target.get("next_question") or "Could you please share the current broker pack and cooperation process?"
    if (target.get("reply_count") or 0) == 0:
        return (
            f"Hi {developer} team, this is Vladimir from Hugs Management in Phuket.\n\n"
            "We have an agency division and are updating our internal developer database and agent training materials.\n\n"
            "Could you please share your current broker/agent pack, price list, availability, payment schedule, "
            "client registration rules and commission/cooperation terms?\n\n"
            "Thank you."
        )
    return (
        "Thank you, received.\n\n"
        f"Could I please confirm one point for our Hugs Management agent database: {next_question}\n\n"
        "Thank you."
    )


def developer_research_feed():
    con = db()
    rows = con.execute(
        """
        SELECT t.*,
               (
                 SELECT text FROM developer_research_events e
                 WHERE e.wa_id = t.wa_id
                 ORDER BY e.created_at DESC
                 LIMIT 1
               ) AS last_research_text,
               (
                 SELECT created_at FROM developer_research_events e
                 WHERE e.wa_id = t.wa_id
                 ORDER BY e.created_at DESC
                 LIMIT 1
               ) AS last_event_at
        FROM developer_research_targets t
        ORDER BY
          CASE t.status
            WHEN 'replied' THEN 1
            WHEN 'outreach_sent' THEN 2
            WHEN 'target' THEN 3
            ELSE 4
          END,
          COALESCE(t.last_reply_at, t.last_outreach_at, t.updated_at) DESC
        """
    ).fetchall()
    con.close()

    items = []
    for row in rows:
        target = dict(row)
        items.append(
            {
                **target,
                "suggested_followup": developer_research_followup_draft(target, target.get("last_research_text") or ""),
            }
        )
    return items


def developer_research_stats():
    con = db()
    row = con.execute(
        """
        SELECT
            COUNT(*) AS targets,
            SUM(CASE WHEN last_outreach_at IS NOT NULL THEN 1 ELSE 0 END) AS outreach_sent,
            SUM(CASE WHEN COALESCE(reply_count, 0) > 0 THEN 1 ELSE 0 END) AS replies,
            SUM(CASE WHEN COALESCE(materials_received, 0) > 0 THEN 1 ELSE 0 END) AS materials_received
        FROM developer_research_targets
        """
    ).fetchone()
    con.close()
    stats = dict(row)
    sent = stats.get("outreach_sent") or 0
    replies = stats.get("replies") or 0
    stats["response_rate"] = round((replies / sent) * 100, 1) if sent else 0
    return stats


def render_research_inbox():
    rows = developer_research_feed()
    researched = len(rows)
    outreach_sent = sum(1 for item in rows if item.get("last_outreach_at"))
    replies = sum(1 for item in rows if (item.get("reply_count") or 0) > 0)
    materials = sum(1 for item in rows if item.get("materials_received"))
    response_rate = round((replies / outreach_sent) * 100, 1) if outreach_sent else 0
    body = [
        '<div class="toolbar">',
        '<div class="muted">Developer research cockpit for Hugs Management agent training.</div>',
        '<div><a href="/research-feed">JSON feed</a> · <a href="/research.csv">CSV export</a> · <a href="/inbox">Sales inbox</a></div>',
        "</div>",
        '<section class="panel">',
        "<h2>Training metrics</h2>",
        "<table><tbody>",
        f"<tr><th>Developers tracked</th><td>{researched}</td><th>Outreach sent</th><td>{outreach_sent}</td></tr>",
        f"<tr><th>Replies</th><td>{replies}</td><th>Materials received</th><td>{materials}</td></tr>",
        f"<tr><th>Response rate</th><td>{response_rate}%</td><th>Mode</th><td>research / no auto sales reply</td></tr>",
        "</tbody></table>",
        "</section>",
        "<table>",
        "<thead><tr><th>Developer</th><th>Status</th><th>Replies</th><th>Materials</th><th>Next question</th><th>Last activity</th></tr></thead>",
        "<tbody>",
    ]
    for item in rows:
        body.append(
            "<tr>"
            f"<td><a href=\"/research-target?wa_id={escape(item['wa_id'])}\">{escape(item.get('developer') or '')}</a>"
            f"<div class=\"muted\">{escape(item.get('project') or '')} · {escape(item.get('wa_id') or '')}</div></td>"
            f"<td><span class=\"pill\">{escape(item.get('status') or '')}</span></td>"
            f"<td>{int(item.get('reply_count') or 0)}</td>"
            f"<td>{'yes' if item.get('materials_received') else 'no'}</td>"
            f"<td>{escape(item.get('next_question') or '')}</td>"
            f"<td>{escape(item.get('last_event_at') or item.get('updated_at') or '')}</td>"
            "</tr>"
        )
    if not rows:
        body.append('<tr><td colspan="6" class="muted">No developer research targets yet.</td></tr>')
    body.extend(["</tbody>", "</table>"])
    return page("Canopy Developer Research", "".join(body))


def render_research_target(wa_id):
    con = db()
    target = con.execute(
        "SELECT * FROM developer_research_targets WHERE wa_id = ?",
        (wa_id,),
    ).fetchone()
    events = con.execute(
        "SELECT * FROM developer_research_events WHERE wa_id = ? ORDER BY created_at ASC",
        (wa_id,),
    ).fetchall()
    con.close()
    if not target:
        return page("Research target not found", '<p><a href="/research">Back to research cockpit</a></p><p>Target not found.</p>')
    target_dict = dict(target)
    chunks = [
        '<p><a href="/research">Back to research cockpit</a></p>',
        '<section class="panel">',
        f"<div><strong>{escape(target_dict.get('developer') or '')}</strong> <span class=\"muted\">{escape(wa_id)}</span></div>",
        f"<div>{escape(target_dict.get('project') or '')} · {escape(target_dict.get('area') or '')}</div>",
        f"<div>Status: <span class=\"pill\">{escape(target_dict.get('status') or '')}</span></div>",
        f"<div class=\"muted\">Contact source: {escape(target_dict.get('contact_source') or '')}</div>",
        "</section>",
        '<section class="panel draft">',
        "<h2>Suggested next WhatsApp</h2>",
        f"<pre>{escape(developer_research_followup_draft(target_dict))}</pre>",
        "</section>",
        '<section class="panel">',
        "<h2>Learning checklist</h2>",
        "<ul>",
        "<li>Broker/agent pack received?</li>",
        "<li>Price list and availability received?</li>",
        "<li>Payment schedule clarified?</li>",
        "<li>Commission/cooperation terms clarified?</li>",
        "<li>Client registration rule and protection period clarified?</li>",
        "<li>Buyer-facing materials permission clarified?</li>",
        "<li>Site inspection / product-learning visit process clarified?</li>",
        "</ul>",
        "</section>",
        '<section class="panel">',
        "<h2>Research events</h2>",
    ]
    for event in events:
        item = dict(event)
        chunks.append(
            '<div class="msg">'
            f"<div><strong>{escape(item.get('direction') or '')}</strong> "
            f"<span class=\"pill\">{escape(item.get('event_type') or '')}</span> "
            f"<span class=\"muted\">{escape(item.get('created_at') or '')}</span></div>"
            f"<pre>{escape(item.get('text') or '')}</pre>"
            "</div>"
        )
    if not events:
        chunks.append('<div class="muted">No research events yet.</div>')
    chunks.append("</section>")
    return page(f"Research: {target_dict.get('developer') or wa_id}", "".join(chunks))


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_authorized_json(self):
        if not SEND_API_TOKEN:
            self.send_json(503, {"error": "BRIDGE_SEND_TOKEN is not configured"})
            return None
        provided = self.headers.get("X-Bridge-Token", "")
        if provided != SEND_API_TOKEN:
            self.send_json(403, {"error": "forbidden"})
            return None
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid json"})
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/flipbook":
            self.send_response(302)
            self.send_header("Location", "/flipbook/")
            self.end_headers()
            return
        if parsed.path.startswith("/flipbook/"):
            rel_path = unquote(parsed.path[len("/flipbook/"):]) or "index.html"
            if rel_path.startswith("/") or ".." in Path(rel_path).parts:
                self.send_json(403, {"error": "forbidden"})
                return
            root = (ASSET_DIR / "flipbook").resolve()
            asset_path = (root / rel_path).resolve()
            if asset_path.is_dir():
                asset_path = asset_path / "index.html"
            if not str(asset_path).startswith(str(root)) or not asset_path.exists() or not asset_path.is_file():
                self.send_json(404, {"error": "flipbook asset not found"})
                return
            content_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
            body = asset_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/assets/"):
            name = Path(parsed.path).name
            asset_path = ASSET_DIR / name
            if not asset_path.exists() or not asset_path.is_file():
                self.send_json(404, {"error": "asset not found"})
                return
            content_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
            body = asset_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "db_path": DB_PATH,
                    "graph_version": os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0"),
                    "phone_number_id": os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
                    "render_git_commit": os.environ.get("RENDER_GIT_COMMIT", ""),
                    "render_service_name": os.environ.get("RENDER_SERVICE_NAME", ""),
                    "test_autoreply_enabled": ENABLE_TEST_AUTOREPLY,
                    "test_autoreply_require_explicit_prefix": TEST_AUTOREPLY_REQUIRE_EXPLICIT_PREFIX,
                    "test_autoreply_wa_ids": sorted(TEST_AUTOREPLY_WA_IDS),
                    "ai_agent_enabled": ENABLE_AI_AGENT,
                    "bridge_autonomous_replies_enabled": ENABLE_BRIDGE_AUTONOMOUS_REPLIES,
                    "ai_audio_transcription_enabled": ENABLE_AI_AUDIO_TRANSCRIPTION,
                    "ai_agent_tools_enabled": ENABLE_AI_AGENT_TOOLS,
                    "agent_welcome_pack_approved": AGENT_WELCOME_PACK_APPROVED,
                    "ai_agent_dry_run": AI_AGENT_DRY_RUN,
                    "ai_agent_model": AI_AGENT_MODEL,
                    "ai_operator_wa_ids": sorted(AI_OPERATOR_WA_IDS),
                    "has_openai_api_key": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                    "has_bridge_send_token": bool(SEND_API_TOKEN),
                    "has_whatsapp_access_token": bool(os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()),
                    "developer_research": developer_research_stats(),
                },
            )
            return
        if parsed.path == "/whatsapp-diagnostics":
            diagnostics = whatsapp_diagnostics(params.get("waba_id", [""])[0])
            self.send_json(200 if diagnostics.get("ok") else 502, diagnostics)
            return
        if parsed.path == "/waba-subscription-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            status = waba_webhook_subscription("status", params.get("waba_id", [""])[0])
            self.send_json(200 if status.get("ok") else 502, status)
            return
        if parsed.path == "/templates":
            if not SEND_API_TOKEN:
                self.send_json(503, {"error": "BRIDGE_SEND_TOKEN is not configured"})
                return
            provided = self.headers.get("X-Bridge-Token", "")
            if provided != SEND_API_TOKEN:
                self.send_json(403, {"error": "forbidden"})
                return
            result = whatsapp_templates(params.get("name", [""])[0])
            self.send_json(200 if result.get("ok") else 502, result)
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
        if parsed.path == "/leads.csv":
            headers = [
                "wa_id",
                "profile_name",
                "segment",
                "priority",
                "last_message_at",
                "next_action",
                "escalation_required",
                "updated_at",
            ]
            con = db()
            rows = con.execute(
                "SELECT * FROM contacts ORDER BY priority ASC, last_message_at DESC"
            ).fetchall()
            con.close()
            send_csv(self, rows_to_csv(headers, rows), "canopy_leads.csv")
            return
        if parsed.path == "/messages.csv":
            headers = [
                "id",
                "wa_id",
                "direction",
                "message_type",
                "text",
                "received_at",
            ]
            con = db()
            rows = con.execute(
                "SELECT id, wa_id, direction, message_type, text, received_at FROM messages ORDER BY received_at DESC"
            ).fetchall()
            con.close()
            send_csv(self, rows_to_csv(headers, rows), "canopy_messages.csv")
            return
        if parsed.path == "/operator-feed":
            self.send_json(200, operator_feed(params.get("limit", ["20"])[0]))
            return
        if parsed.path == "/research-feed":
            self.send_json(200, developer_research_feed())
            return
        if parsed.path == "/research.csv":
            headers = [
                "developer",
                "project",
                "area",
                "wa_id",
                "status",
                "last_outreach_at",
                "last_reply_at",
                "reply_count",
                "materials_received",
                "next_question",
                "updated_at",
            ]
            con = db()
            rows = con.execute(
                """
                SELECT developer, project, area, wa_id, status, last_outreach_at,
                       last_reply_at, reply_count, materials_received, next_question, updated_at
                FROM developer_research_targets
                ORDER BY updated_at DESC
                """
            ).fetchall()
            con.close()
            send_csv(self, rows_to_csv(headers, rows), "canopy_developer_research.csv")
            return
        if parsed.path == "/events.csv":
            headers = ["id", "received_at", "raw_json"]
            con = db()
            rows = con.execute(
                "SELECT id, received_at, raw_json FROM webhook_events ORDER BY id DESC LIMIT 200"
            ).fetchall()
            con.close()
            send_csv(self, rows_to_csv(headers, rows), "canopy_events.csv")
            return
        if parsed.path == "/" or parsed.path == "/inbox":
            body = render_inbox()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/research":
            body = render_research_inbox()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/research-target":
            body = render_research_target(params.get("wa_id", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/playbook":
            body = render_playbook()
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
        if parsed.path == "/ai-agent-events":
            con = db()
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wa_id TEXT NOT NULL,
                    inbound_message_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reply TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            rows = con.execute(
                "SELECT * FROM ai_agent_events ORDER BY id DESC LIMIT 50"
            ).fetchall()
            con.close()
            body = rows_to_json(rows)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/transcribe-latest-vladimir-voice-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = transcribe_latest_audio_for("66628512432")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, **result})
            return
        if parsed.path == "/process-latest-vladimir-voice-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = process_latest_audio_for("66628512432")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, **result})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/send-agent-packet-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = send_agent_packet_test()
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/send-agent-welcome-pack-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_welcome_pack("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/send-agent-layouts-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_layout_documents("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "results": result})
            return
        if path == "/send-delivery-ping-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_delivery_ping_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v2-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_intro_carousel_8_v2")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v2-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_intro_carousel_8_v2")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v2-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v2_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v3-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_intro_carousel_10_v3")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v3-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_intro_carousel_10_v3")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v3-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v3_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v4-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_intro_carousel_10_v4")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v4-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_intro_carousel_10_v4")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v4-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v4_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v5-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_intro_carousel_10_v5")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v5-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_intro_carousel_10_v5")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v5-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v5_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v6-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_intro_carousel_10_v6")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v6-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_intro_carousel_10_v6")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v6-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v6_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/create-carousel-v7-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v1_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v1_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/carousel-v7-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v1")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v7-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v8-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v2_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v2_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/carousel-v8-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v2")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v8-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v9-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v3_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v3_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/carousel-v9-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v3")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v9-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v10-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v4_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v4_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/carousel-v10-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v4")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v10-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v11-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v5_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v5_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/carousel-v11-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v5")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v11-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v12-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            en_result = create_canopy_template("agent_advantages_carousel_10_v6_en")
            ru_result = create_canopy_template("agent_advantages_carousel_10_v6_ru")
            ok = bool(en_result.get("ok") and ru_result.get("ok"))
            self.send_json(200 if ok else 502, {"ok": ok, "en": en_result, "ru": ru_result})
            return
        if path == "/create-carousel-v12-en-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_advantages_carousel_10_v6_en")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/create-carousel-v12-ru-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_advantages_carousel_10_v6_ru")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v12-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v6")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v12-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-carousel-v14-en-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_advantages_carousel_10_v7_en")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/create-carousel-v14-ru-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_advantages_carousel_10_v7_ru")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/carousel-v14-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_advantages_carousel_10_v7")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-carousel-v14-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_carousel_v7("66628512432", "ru")
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-agent-video-cta-template-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = create_canopy_template("agent_video_intro_cta_v1")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/agent-video-cta-template-status-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = whatsapp_templates("canopy_agent_video_intro_cta_v1")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-agent-video-cta-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_video_cta_template_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/send-agent-intro-video-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = send_agent_intro_video_test()
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/send-agent-welcome-pack":
            payload = self.read_authorized_json()
            if payload is None:
                return
            to = str(payload.get("to", "")).strip()
            language = str(payload.get("language", "") or "en").strip().lower()
            if language in {"rus", "russian"}:
                language = "ru"
            if language in {"zh", "cn", "ch", "chi", "chinese"}:
                language = "zh"
            if language not in {"en", "ru", "zh"}:
                language = "en"
            if not to:
                self.send_json(400, {"error": "to is required"})
                return
            try:
                result = send_agent_welcome_pack(to, language)
            except Exception as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
                return
            self.send_json(200, {"ok": all(item.get("ok") for item in result), "results": result})
            return
        if path == "/send-text":
            payload = self.read_authorized_json()
            if payload is None:
                return
            to = str(payload.get("to", "")).strip()
            text = str(payload.get("text", "")).strip()
            if not to or not text:
                self.send_json(400, {"error": "to and text are required"})
                return
            try:
                result = send_whatsapp_text(to, text)
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/send-vladimir-text-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid json"})
                return
            text = str(payload.get("text", "")).strip()
            if not text:
                self.send_json(400, {"error": "text is required"})
                return
            try:
                result = send_whatsapp_text("66628512432", text)
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/send-canopy-market-intel-outreach-20260617":
            self.send_json(
                410,
                {
                    "ok": False,
                    "error": "disabled",
                    "reason": "Hugs Management developer research outreach must use a separate dedicated WhatsApp channel, not the Canopy Hills bridge.",
                },
            )
            return
        if path == "/send-template":
            payload = self.read_authorized_json()
            if payload is None:
                return
            to = str(payload.get("to", "")).strip()
            template_name = str(payload.get("template", "") or payload.get("name", "")).strip()
            language_code = str(payload.get("language", "") or payload.get("language_code", "") or "en_US").strip()
            components = payload.get("components")
            if not to or not template_name:
                self.send_json(400, {"error": "to and template are required"})
                return
            if components is not None and not isinstance(components, list):
                self.send_json(400, {"error": "components must be a list when provided"})
                return
            try:
                result = send_whatsapp_template(to, template_name, language_code, components)
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/create-canopy-template":
            payload = self.read_authorized_json()
            if payload is None:
                return
            template_key = str(payload.get("template_key", "") or payload.get("key", "")).strip()
            if not template_key:
                self.send_json(400, {"error": "template_key is required"})
                return
            result = create_canopy_template(template_key)
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path == "/send-media":
            payload = self.read_authorized_json()
            if payload is None:
                return
            to = str(payload.get("to", "")).strip()
            media_type = str(payload.get("type", "") or payload.get("media_type", "")).strip()
            link = str(payload.get("link", "") or payload.get("url", "")).strip()
            caption = str(payload.get("caption", "")).strip()
            filename = str(payload.get("filename", "")).strip()
            if not to or not media_type or not link:
                self.send_json(400, {"error": "to, type and link are required"})
                return
            try:
                result = send_whatsapp_media(to, media_type, link, caption, filename)
            except Exception as exc:
                self.send_json(502, {"error": str(exc)})
                return
            self.send_json(200, {"ok": True, "meta": result})
            return
        if path == "/subscribe-current-waba-test":
            if self.headers.get("X-Agent-Test", "") != "canopy-agent-packet-v1":
                self.send_json(401, {"error": "unauthorized"})
                return
            result = waba_webhook_subscription("subscribe")
            self.send_json(200 if result.get("ok") else 502, result)
            return
        if path != "/webhook":
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
