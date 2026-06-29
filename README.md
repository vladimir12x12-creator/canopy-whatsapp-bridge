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
- `GET /operator-feed?limit=20` - compact AI/operator queue with latest inbound messages, classification, suggested reply, and suggested materials.
- `GET /research` - Hugs Management developer-research cockpit for Canopy Phuket Agent training.
- `GET /research-feed` - JSON developer-research feed with metrics, next questions, and suggested follow-ups.
- `GET /research.csv` - CSV export for developer outreach/reply/material status.
- `GET /ai-agent-events` - recent AI-agent send/dry-run/error log.
- `GET /health` - deployment and env diagnostics.
- `GET /templates?name=...` - protected template status check from Meta WhatsApp Manager.
- `POST /send-text` - disabled legacy free-text relay; returns 410 because WhatsApp is transport only.
- `POST /codex-manual-text` - protected manual Codex text to Vladimir/operator only.
- `POST /codex-manual-lead-text` - protected manual Codex text to an external lead/contact with an existing inbound conversation and an open 24-hour customer-service window.
- `POST /codex-manual-lead-agent-welcome-pack` - protected manual agent package attempt for an external lead/contact with an existing inbound conversation and an open 24-hour customer-service window. Current carousel may return a blocker if the only available template has a visible dot body.
- `POST /codex-manual-lead-client-welcome-pack` - protected manual client package attempt for an external lead/contact with an existing inbound conversation and an open 24-hour customer-service window. Current carousel may return a blocker if the only available template has a visible dot body.
- `POST /send-template` - protected outbound template send for first contact or closed windows.
- `POST /send-media` - protected outbound image/video/document send by public HTTPS link. Use only inside a 24-hour customer-service window.
- `POST /create-canopy-template` - protected helper to submit built-in Canopy templates to Meta.
- `POST /send-agent-welcome-pack-test` - protected staging helper to send the agreed agent welcome pack to Vladimir.
- `GET /transcribe-latest-vladimir-voice-test` - protected staging helper to download Vladimir's latest WhatsApp voice note, transcribe it with OpenAI, update the stored message text, and reclassify the contact.
- `GET /process-latest-vladimir-voice-test` - protected staging helper to transcribe Vladimir's latest WhatsApp voice note and run it through the AI reply flow.

Voice note transcription requires these Render environment variables:

- `OPENAI_API_KEY` - OpenAI API key for transcription.
- `OPENAI_TRANSCRIBE_MODEL` - optional, defaults to `gpt-4o-mini-transcribe`.
- `OPENAI_TRANSCRIBE_LANGUAGE` - optional, defaults to `ru`.
- `ENABLE_AI_AUDIO_TRANSCRIPTION=1` - transcribe inbound WhatsApp voice notes and pass the transcript to the AI agent. Defaults to `1` in code.

## WhatsApp Transport Mode

Production decision:

- the webhook bridge is transport only;
- the bridge receives/stores WhatsApp messages and exposes protected outbound send endpoints;
- the bridge's old autonomous reply path stays disabled;
- the previous `whatsapp_bridge/codex_relay_runner.py` sidecar is legacy and is not started in production;
- Codex/project context is the brain; WhatsApp is only the channel.

Keep these disabled for normal operation:

- `ENABLE_BRIDGE_AUTONOMOUS_REPLIES=0`;
- `ENABLE_AI_AGENT_TOOLS=0`.

Production check after deploy:

```bash
curl -sS https://canopy-whatsapp-bridge.onrender.com/health
curl -sS 'https://canopy-whatsapp-bridge.onrender.com/operator-feed?limit=5'
curl -sS https://canopy-whatsapp-bridge.onrender.com/ai-agent-events
```

Expected `/health` values for transport mode:

- `render_git_commit` should match the latest GitHub commit.
- `bridge_autonomous_replies_enabled` should be `false`.
- `ai_agent_tools_enabled` should be `false`.

Staging voice transcription test:

```bash
curl -sS https://canopy-whatsapp-bridge.onrender.com/transcribe-latest-vladimir-voice-test \
  -H "X-Agent-Test: canopy-agent-packet-v1"

curl -sS https://canopy-whatsapp-bridge.onrender.com/process-latest-vladimir-voice-test \
  -H "X-Agent-Test: canopy-agent-packet-v1"
```

## Current Blockers

1. Render deploy is currently stale.
   - GitHub `main` has newer commits than Render.
   - Render `/health` still reports old commit `584ed3cdd5cef7d87a9f50f89e8a46091349eaf2`.
   - Manual deploy is required in Render: service `canopy-whatsapp-bridge` -> `Manual Deploy` -> `Deploy latest commit`.
2. WhatsApp payment method.
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

## Developer Research Mode

Status: deprecated for Hugs Management outreach.

Purpose was to let the Canopy Phuket Agent learn from real developer WhatsApp conversations without mixing those conversations with buyer leads. Vladimir later decided this must not use the Canopy Hills WhatsApp bridge or number. Hugs Management developer research must run on a separate dedicated WhatsApp channel/bridge.

Current cockpit:

- `https://canopy-whatsapp-bridge.onrender.com/research`
- `https://canopy-whatsapp-bridge.onrender.com/research-feed`
- `https://canopy-whatsapp-bridge.onrender.com/research.csv`

Tracked starter targets:

- Botanica Luxury Villas
- Anchan Villas
- Trichada Villas
- Andaman Asset Solution / The Trinity Village
- Mouana Phuket

Behavior:

- Known developer numbers are logged into `developer_research_targets` and `developer_research_events`.
- Inbound developer replies are not answered by the normal Canopy sales AI.
- The bridge records whether replies mention broker materials, price list, availability, payment schedule, commission, client registration, MOU/agency agreement, or site inspection.
- The cockpit generates a suggested next WhatsApp follow-up for each developer.
- Daily reporting can read `developer_research` from `/health` or the detailed list from `/research-feed`.

Outbound constraint:

- Do not use `canopy-whatsapp-bridge` for Hugs Management developer outreach.
- The staging Cloud API number `+1 555 639 8541` is still limited by Meta recipient permissions. It can send to allowed/operator recipients, but external developer numbers returned `(#131030) Recipient phone number not in allowed list`.
- For external Canopy lead tests, first verify the contact appears in `/operator-feed` or `/messages?wa_id=...`; then use `/codex-manual-lead-text` while the 24-hour customer-service window is open. Do not look only at Vladimir's `wa_id`, because a real lead appears as a separate conversation.
- The one-off market-intel batch endpoint is disabled and returns 410.
- Build a separate Hugs Management bridge/service for the dedicated research number.

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

Submit built-in Canopy templates from Render Shell:

```bash
curl -sS -X POST https://canopy-whatsapp-bridge.onrender.com/create-canopy-template \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Token: $BRIDGE_SEND_TOKEN" \
  --data '{"template_key":"agent_saleskit_intro"}'

curl -sS -X POST https://canopy-whatsapp-bridge.onrender.com/create-canopy-template \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Token: $BRIDGE_SEND_TOKEN" \
  --data '{"template_key":"vladimir_need_reply"}'
```

Built-in template keys:

- `agent_saleskit_intro` - first broker/agent message with project summary and Sales Kit button.
- `vladimir_need_reply` - short Russian ping asking Vladimir to reply in WhatsApp so the 24-hour conversation window opens.
