# Canopy WhatsApp Cloud API Staging Runbook

Date: 2026-06-03

## Purpose

Build and test the WhatsApp Cloud API automation on a non-live number before touching the live sales WhatsApp.

## Live Number - Do Not Touch Without Explicit Decision

- Number: `+66 61 997 8591`
- Meta asset: `Canopy Hills Phuket — WhatsApp Business App`
- Role: current live sales / lead number
- Rule: do not migrate, disconnect, or reconfigure destructively.

## Staging Cloud API Number

- Number: `+1 555 639 8541`
- Meta asset: `Test WhatsApp Business Account`
- WABA ID: `2253327871868025`
- Phone Number ID: `1021241121083612`
- Meta app used: `VMB`
- App ID: `1693287358483119`
- Business ID: `1452386178649897`
- API version shown by Meta: `v25.0`
- Allowed test recipient used for Vladimir: `+66 62 851 2432` / `66628512432`

## Completed

- Meta app was published.
- Privacy policy URL was set: `https://canopy.villas/privacy`
- User data deletion URL was set: `https://canopy.villas/privacy`
- App category was set to `Business and pages`.
- Webhook was verified successfully during temporary tunnel test.
- Webhook field `messages` was subscribed.
- Local webhook prototype stores inbound webhook payloads in SQLite.
- Render staging bridge is deployed at `https://canopy-whatsapp-bridge.onrender.com`.
- Render service is on a paid plan with persistent SQLite storage at `/var/data/leads.sqlite`.
- Outbound text endpoint works technically, but first business-initiated messages should use WhatsApp approved templates. A direct free-text test can be accepted by Meta and still not appear if the 24-hour customer-service window is not open.
- Outbound template sending was verified with Meta's `hello_world` template.

## Local Webhook Prototype

Run locally:

```bash
VERIFY_TOKEN=canopy-whatsapp-verify-2026-06-03 WHATSAPP_BRIDGE_PORT=8088 python3 whatsapp_bridge/webhook_server.py
```

Endpoints:

- `GET /webhook` - Meta verification endpoint.
- `POST /webhook` - inbound WhatsApp webhook receiver.
- `GET /leads` - classified contacts.
- `GET /messages?wa_id=...` - messages for one contact.
- `GET /templates?name=...` - protected template status check from Meta WhatsApp Manager.
- `POST /send-text` - protected outbound free-text send. Use only inside a 24-hour customer-service window.
- `POST /send-template` - protected outbound template send for first contact or closed windows.
- `POST /send-media` - protected outbound image/video/document send by public HTTPS link. Use only inside a 24-hour customer-service window.

## Current Blockers

1. WhatsApp payment method.
   - Required for business-initiated conversations and template sends.
   - Not required for receiving inbound webhook messages.
   - This still matters before moving to the live number.

## Hosting Decision Needed

Pick one permanent webhook option:

1. `Render/Railway/Fly.io` small Python service.
   - Fastest practical route.
   - Good enough for staging and early production.

2. Cloudflare Worker.
   - Very lightweight and robust.
   - Would need rewriting the Python bridge logic or forwarding into a datastore.

3. VPS.
   - Maximum control.
   - More operations overhead.

Recommended next step: keep testing on the staging number, but move the bridge to persistent storage before using it as the operational lead inbox.

## Next Technical Steps

1. Create Canopy-specific WhatsApp message templates in Meta.
2. Prepare approved media templates for first-contact render/video previews.
3. Build lead scenario flows for agent/client/junk classification.
4. Test the flow fully on the staging WABA.
5. Decide when and how to move from the test number to the live ad number `+66 61 997 8591`.

## Permanent Token Path

Use the existing system user `CanopyBot`:

1. Open Meta Business Settings for business `1452386178649897`.
2. Go to `Users` -> `System users`.
3. Select `CanopyBot`.
4. Make sure the WhatsApp Business Account `2253327871868025` is assigned to this system user with messaging/management access.
5. Click `Generate token`.
6. Select app `VMB` / app ID `1693287358483119`.
7. Select permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
8. Generate the token.
9. Do not paste the token into normal chat history. Put it directly into Render as `WHATSAPP_ACCESS_TOKEN`, or into a local shell variable only for a one-off test.

Local outbound smoke test after token exists:

```bash
WHATSAPP_ACCESS_TOKEN='...' \
WHATSAPP_PHONE_NUMBER_ID=1021241121083612 \
python3 whatsapp_bridge/send_text.py 66628512432 'Test message from Canopy WhatsApp Cloud API staging.'
```

Protected template test from Render Shell:

```bash
curl -sS -X POST https://canopy-whatsapp-bridge.onrender.com/send-template \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Token: $BRIDGE_SEND_TOKEN" \
  --data '{"to":"66628512432","template":"hello_world","language":"en_US"}'
```

Protected template status check from Render Shell:

```bash
curl -sS "https://canopy-whatsapp-bridge.onrender.com/templates?name=canopy_broker_preview_august" \
  -H "X-Bridge-Token: $BRIDGE_SEND_TOKEN"
```
