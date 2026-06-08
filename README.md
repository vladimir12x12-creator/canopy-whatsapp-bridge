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
- Render staging bridge is deployed at `https://canopy-whatsapp-bridge.onrender.com`.
- Permanent storage requires a paid Render web service plus persistent disk. Free Render web services use an ephemeral filesystem and lose local SQLite data after sleep/restart/redeploy.

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

1. Permanent storage for leads/messages.
   - The service uses SQLite.
   - On Render Free, `/tmp` and other local filesystem changes are ephemeral.
   - Production-ready option: Render Starter web service plus a 1GB persistent disk mounted at `/var/data`, with `WHATSAPP_BRIDGE_DB=/var/data/leads.sqlite`.

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

Recommended next step: keep testing on the staging number, but move the bridge to persistent storage before using it as the operational lead inbox.

## Next Technical Steps

1. Confirm paid Render Starter + 1GB persistent disk for reliable storage.
2. Sync updated `render.yaml`.
3. Confirm `https://canopy-whatsapp-bridge.onrender.com/inbox` keeps leads after restart.
4. Generate permanent system-user token for `CanopyBot`.
5. Add `WHATSAPP_ACCESS_TOKEN` to Render environment variables.
6. Send a real outbound test message to Vladimir's staging WhatsApp.
7. Add media sending and template support.

## Permanent Token Path

Use the existing system user `CanopyBot`:

1. Open Meta Business Settings for business `1452386178649897`.
2. Go to `Users` -> `System users`.
3. Select `CanopyBot`.
4. Make sure the WhatsApp Business Account `2097915004106030` is assigned to this system user with messaging/management access.
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
WHATSAPP_PHONE_NUMBER_ID=1183823618137845 \
python3 whatsapp_bridge/send_text.py 66628512432 'Test message from Canopy WhatsApp Cloud API staging.'
```
