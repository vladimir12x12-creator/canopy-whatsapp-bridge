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

- Number: `+66 98 098 7456`
- Meta asset: `Canopy Hills Villas Phuket`
- WABA ID: `2097915004106030`
- Phone Number ID: `1183823618137845`
- Meta app used: `VMB`
- App ID: `1693287358483119`
- Business ID: `1452386178649897`
- API version shown by Meta: `v25.0`

## Completed

- Meta app was published.
- Privacy policy URL was set: `https://canopy.villas/privacy`
- User data deletion URL was set: `https://canopy.villas/privacy`
- App category was set to `Business and pages`.
- Webhook was verified successfully during temporary tunnel test.
- Webhook field `messages` was subscribed.
- Local webhook prototype stores inbound webhook payloads in SQLite.

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

## Current Blockers

1. Permanent HTTPS webhook endpoint.
   - Temporary Cloudflare quick tunnel was only for testing and is not production.
   - Meta currently needs a stable callback URL.

2. Permanent/system-user access token.
   - Needed for outbound API sending.
   - Temporary token generation did not proceed from the Developer console UI.
   - Use Business Settings / System Users path after passkey if needed.

3. WhatsApp payment method.
   - Required for business-initiated conversations and template sends.
   - Not required for receiving inbound webhook messages.

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

Recommended next step: use a small hosted Python service for staging, then upgrade if volume requires it.

## Next Technical Steps

1. Choose hosting target.
2. Deploy `webhook_server.py` or an equivalent service.
3. Set Meta webhook callback URL to the permanent `/webhook` endpoint.
4. Re-run Meta webhook verification.
5. Send a real inbound message to the staging number.
6. Confirm it appears in SQLite / lead inbox.
7. Add outbound sender only after a permanent token exists.
