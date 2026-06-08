#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request


GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "1183823618137845")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def send_text(to, body):
    if not ACCESS_TOKEN:
        raise SystemExit(
            "WHATSAPP_ACCESS_TOKEN is not set. Create a permanent system-user token "
            "for the staging WABA and export it before sending."
        )
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": True, "body": body},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            print(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        raise


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: send_text.py <wa_id> <message text>")
    send_text(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()

