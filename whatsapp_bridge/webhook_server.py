#!/usr/bin/env python3
import json
import os
import sqlite3
import csv
import io
import urllib.error
import urllib.request
from html import escape
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

DB_PATH = os.environ.get(
    "WHATSAPP_BRIDGE_DB",
    "/Users/vm/Documents/Codex/2026-05-27/new-chat/whatsapp_bridge/leads.sqlite",
)
HOST = os.environ.get("WHATSAPP_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHATSAPP_BRIDGE_PORT", "8088"))
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")
SEND_API_TOKEN = os.environ.get("BRIDGE_SEND_TOKEN", "")
DEFAULT_WABA_ID = os.environ.get("WHATSAPP_WABA_ID", "2253327871868025")


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
    if has_any(t, ["details", "send materials", "project materials", "sales kit", "salekit", "brochure", "presentation", "deck", "pdf", "send info", "send more", "share with my client", "подроб", "пришлите материалы", "отправьте материалы", "материалы по проекту", "презентац", "брошюр", "информац"]):
        return {
            "segment": "materials_request",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send welcome capsule with approved renders/media, then qualify buyer vs agent/client.",
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
    if has_any(t, ["agent", "broker", "agency", "agencies", "commission", "realtor", "агент", "брокер", "комисс"]):
        return {
            "segment": "broker",
            "priority": "P2",
            "escalation_required": 0,
            "next_action": "Send broker pack: availability, 6% commission, client registration.",
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
                "Земля находится у Hugs Management Co., Ltd.; структура сделки предусматривает договор на виллу и leasehold на земельный участок. "
                "Для предметного обсуждения можем подготовить юридический пакет и отдельный созвон с командой проекта.\n\n"
                "У вас уже есть юрист/консультант, который будет смотреть документы?"
            )
        return (
            "I understand the question. For a serious buyer, legal and construction due diligence is as important as the layout.\n\n"
            "The land is held by Hugs Management Co., Ltd.; the transaction structure uses a villa sale agreement and leasehold for the land plot. "
            "For a serious review, we can prepare a legal package and arrange a call with the project team.\n\n"
            "Do you already have a lawyer/advisor who will review the documents?"
        )
    if segment == "materials_request":
        if ru:
            return (
                "Конечно, отправлю короткую информацию по Canopy Hills и несколько рендеров, которые удобно переслать клиенту или коллеге.\n\n"
                "Canopy Hills Villas - клубный поселок из 9 премиальных вилл на холме напротив British International School Phuket. "
                "Проект создан для долгосрочной семейной жизни: просторные виллы 650-745 м², панорамные виды, приватность, тишина и повседневная инфраструктура рядом.\n\n"
                "Главное отличие - сочетание BISP-location, просторных помещений, вида и качества строительства: продуманная инженерия, "
                "термо- и шумоизоляция, качественные материалы, зоны хранения и планировки для реальной жизни семьи.\n\n"
                "Вилла C9 будет готова в начале августа, строительство следующих вилл уже начато.\n\n"
                "Подскажите, пожалуйста, у вас уже есть конкретный клиент под Canopy Hills или вы хотите получить материалы для базы?"
            )
        return (
            "Sure, I will share a short Canopy Hills summary and several renders that are easy to forward to a client or colleague.\n\n"
            "Canopy Hills Villas is a club-style estate of 9 premium hillside villas opposite British International School Phuket. "
            "The project is designed for long-term family living: spacious 650-745 sqm homes, panoramic views, privacy, quiet surroundings and everyday infrastructure nearby.\n\n"
            "The key difference is the combination of BISP location, spacious interiors, views and construction quality: thoughtful engineering, "
            "thermal and sound insulation, high-quality materials, storage areas and layouts made for real family life.\n\n"
            "Villa C9 will be ready in early August, and construction of the next villas has already started.\n\n"
            "Do you already have a specific client for Canopy Hills, or would you like the materials for your database?"
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
                "Мы работаем с агентами на комиссии 6%. Можем отправить актуальную доступность, прайс и правила регистрации клиента.\n\n"
                "Подскажите, пожалуйста, у вас уже есть конкретный клиент под проект или вы хотите получить материалы для базы?"
            )
        return (
            "Hi, thank you for contacting Canopy Hills.\n\n"
            "We work with agents on a 6% commission basis. We can share current availability, price list, and client registration details.\n\n"
            "Do you already have a specific client for the project, or would you like the materials for your database?"
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
            "Welcome capsule text RU/EN.",
            "4 approved renders: overall, terrace/view, living/kitchen, evening exterior.",
            "Agents: forwardable broker intro plus SalesKit link.",
            "Clients: relevant RU/EN/CH presentation, not full SalesKit.",
            "Then ask: specific client or materials for database?",
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
                },
            )
            return
        if parsed.path == "/whatsapp-diagnostics":
            diagnostics = whatsapp_diagnostics(params.get("waba_id", [""])[0])
            self.send_json(200 if diagnostics.get("ok") else 502, diagnostics)
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
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
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
