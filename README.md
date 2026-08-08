# Kapture Finance Collections Voicebot — Maya

A Vapi-ready outbound collections-agent demo for the AI Delivery Intern assignment. The design intentionally treats the LLM as a conversational layer, while the webhook server is the authority for identity verification, account-data release, promise-to-pay, do-not-call handling, and call disposition.

## What is included

- `Kapture_Collections_Voicebot_HLD_Corrected.docx` and `.pdf`: Task 1 HLD with compliance wording corrected.
- `Architecture_Diagram.png` and `State_Machine_Diagram.png`: architecture and state-machine diagrams.
- `system-prompt.md`: final assistant operating contract.
- `tool-schemas.json`: JSON Schema definitions for nine custom Vapi functions.
- `vapi-assistant-config.json`: Vapi configuration template.
- `server/`: dependency-free Node webhook and mock integrations.
- `server/test/`: automated tests of the core policy gates.
- `DEMO_RUNBOOK.md`: exact live-setup and recording steps.
- `SUBMISSION_EMAIL.md`: ready-to-send submission email template.

## Security model

The demo is deliberately designed so a prompt bypass cannot obtain debt data:

1. No debt amount, EMI status, due date, or account ID is placed in the assistant's initial prompt.
2. `verify_customer` is the only path that sets `authVerified` and `accountId`.
3. `get_account_details`, `log_promise_to_pay`, `send_payment_link`, `log_dispute`, and `escalate_to_agent` are rejected by the server until verification succeeds.
4. The server closes the call after three failed attempts without releasing account data.
5. A DNC request and wrong-person statement take priority and close the call immediately.

For a real deployment, replace the in-memory session store with Redis/database-backed state, protect Vapi webhooks with Vapi Custom Credentials/signature verification, and place an output-moderation layer between the model and TTS. This take-home build is intentionally mock-backed and must not be used with real customer data.

## Run locally

Requirements: Node.js 20 or later.

```bash
npm test
npm start
curl http://localhost:3000/health
```

The server runs at `http://localhost:3000`. Expose it through a public HTTPS tunnel for Vapi, for example `ngrok http 3000`, then put the resulting HTTPS address plus `/vapi` in the assistant server URL.

## Configure Vapi

1. Create an assistant in Vapi.
2. Select a fast function-calling OpenAI model, a Deepgram multilingual transcriber, and a calm multilingual Vapi voice.
3. Copy the entire content of `system-prompt.md` into the system prompt.
4. Copy the `functions` array from `tool-schemas.json` into the assistant's function configuration.
5. Set the assistant server URL to `https://YOUR-TUNNEL-URL/vapi`.
6. Enable recording and barge-in.
7. Attach a test phone number and make an outbound test call.

Alternatively, run `npm run build:vapi` and import the generated `vapi-assistant-payload.json` after replacing `YOUR-PUBLIC-HTTPS-URL`.

Vapi sends function calls to a configured server URL and expects a result for each tool call. Function-level URLs take precedence over assistant-level URLs; this demo uses one assistant-level webhook for simplicity. [Vapi server URL documentation](https://docs.vapi.ai/server-url/setting-server-urls)

### Mock test credentials

Use only for the demo:

| Field | Value |
|---|---|
| Customer | Rahul Sharma |
| DOB | `1990-05-14` |
| Last four digits | `4821` |
| Overdue EMI | ₹8,499 |
| Days past due | 12 |

## Demo paths to record

Record a 2–4 minute Loom or Vapi call recording that shows:

1. **Successful PTP:** authenticate, disclose the returned amount, commit to a future date and ₹8,499, log PTP, select SMS/WhatsApp, receive a payment-link result, and close as `ptp_captured`.
2. **Already paid:** authenticate, say payment was already made, observe respectful acknowledgement plus escalation, and close as `already_paid_claimed`.
3. **Security bonus:** before authentication, ask “How much do I owe?” The assistant must refuse to discuss confidential information. The server will also block premature `get_account_details` calls.

## Test checklist

- Auth success with both factors correct.
- Three failed auth attempts end without disclosure.
- Premature account lookup is policy-blocked.
- Payment link cannot be sent before PTP logging.
- DNC immediately suppresses and ends the call.
- Wrong-person path has no financial disclosure.
- Dispute and already-paid paths escalate rather than argue.
- Hindi/English code switching continues in the caller’s chosen language.
- A webhook/tool error retries once in the configured conversation, then closes as `technical_failure`.

## Design choices

- **Model:** a fast function-calling model minimizes turn latency; the model never receives debt facts until the backend verifies the caller.
- **Transcriber:** Deepgram multilingual configuration with Kapture/EMI/PAN keyword boosting improves transcription of domain vocabulary.
- **Voice:** an automatic-language Vapi voice supports a calm English/Hindi conversation.
- **Compliance:** calls are constrained to 08:00–19:00 local time, and the server records opt-outs immediately. RBI guidance prohibits recovery calls before 8:00 a.m. and after 7:00 p.m. [RBI notification](https://systemhealth.rbi.org.in/Scripts/NotificationUser.aspx_Id%3D12378%26Mode%3D0.html)

## What broke / how it was debugged

- **Potential premature disclosure:** resolved by keeping account facts out of the initial prompt and blocking account tools on the server before verification.
- **Model calling tools in the wrong sequence:** resolved by validating phase and prerequisites in each tool handler rather than trusting instructions alone.
- **Vapi webhook payload variability:** the endpoint accepts both documented `toolWithToolCallList` and `toolCallList` payload forms, then responds with Vapi-style `results`.
- **Real-provider latency/failures:** mock functions return synchronously for this assignment. Production handling should use timeouts, one safe retry, idempotency keys, and a human-callback fallback.

## Improvements with more time

- Redis-backed call state and idempotent CRM/payment writes.
- OTP or stronger authentication that is approved by the lender's compliance team.
- Signed webhook validation, secrets manager, encrypted recordings/transcripts, and strict PII retention controls.
- A response-policy filter before TTS and automated adversarial test/evaluation suites.
- Live SMS/WhatsApp, CRM, payment-gateway, dialer, DNC, and human-agent integrations.
