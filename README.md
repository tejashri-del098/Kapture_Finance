**The webhook is hosted on Render's free tier and may take 30–50s to respond on first call after inactivity. Please allow for this, or hit https://kapture-finance.onrender.com/health first.**

# Kapture Finance Collections Voicebot — Maya

A Vapi-ready outbound collections-agent demo for the AI Delivery Intern assignment. 

The core thesis of this submission is that a collections voicebot **cannot rely on prompting alone for data safety**. This project implements a strict, backend-enforced state machine in FastAPI that absolutely refuses to disclose debt data or send payment links until the caller is cryptographically verified by the server session.

## Demo & Architecture

- **Demo Video:** [Insert Loom link here]
- **HLD Documents:** See `Kapture_Collections_Voicebot_HLD_Corrected.pdf` and the Architecture Diagrams in the root folder.
- **Backend URL:** `https://kapture-finance.onrender.com/vapi`

## Setup & Configuration

1. Create a Vapi Assistant and configure it with an OpenAI model (e.g., `gpt-4o` for fast function calling) and Deepgram Nova-2 transcriber (strong EN/HI code-switching).
2. Set the assistant's System Prompt using the contents of `system-prompt.md`.
3. Add the tools from `tool-schemas.json`.
4. Set the assistant's Server URL to `https://kapture-finance.onrender.com/vapi`.
5. Ensure the Vapi phone number matches the Twilio number if testing the real Twilio SMS fallback.

**Test Credentials:**
- Name: Rahul Sharma
- DOB: `1990-05-14`
- Last 4 digits: `4821`

## Design Choices

- **Architecture:** The control plane (Vapi/LLM orchestration) is completely isolated from the data plane (the FastAPI backend). The LLM is treated as an untrusted client.
- **Model:** GPT-4o is preferred for its superior JSON function-calling reliability and multilingual switching (English to Hindi).
- **Transcriber:** Deepgram Nova-2 handles Indian English accents and Hindi code-switching much better than standard Whisper.
- **Auth Enforcement:** The initial prompt intentionally omits all debt facts. Debt data only enters the LLM's context window *after* a successful `verify_customer` tool call flips the `verified` flag in the FastAPI session store.

## Debugging & Root Cause Analysis

During adversarial testing, I focused specifically on Vapi's orchestration layer by testing **Barge-ins (Over-talking)**. When the bot is interrupted mid-disclosure, the LLM often loses its place and attempts to repeat tool calls out of order.

**Bug / Trace:**
When interrupting the bot immediately after verification ("Wait, how much again?"), the LLM redundantly called `get_account_details` a second time, which in a poorly designed state machine could cause a reset or crash. 

Here is the exact trace from `server.log` showing the barge-in recovery:

```log
2026-08-08 14:17:56,000 REQUEST POST /vapi {"message": {"type": "tool-calls", "callId": "call_bargein_test_09", "toolWithToolCallList": [{"toolCallId": "call_1786178875992", "function": {"name": "verify_customer", "arguments": "{\"dateOfBirth\": \"1990-05-14\", \"idLast4\": \"4821\"}"}}]}}
2026-08-08 14:17:56,001 event=session_created call_id=call_bargein_test_09 phone=None
2026-08-08 14:17:56,001 event=tool_result call_id=call_bargein_test_09 name=verify_customer result={"verified": true, "attemptsLeft": 2, "accountId": "loan_rahul_001"}

# First account details disclosure
2026-08-08 14:17:56,511 REQUEST POST /vapi {"message": {"type": "tool-calls", "callId": "call_bargein_test_09", "toolWithToolCallList": [{"toolCallId": "call_1786178876506", "function": {"name": "get_account_details", "arguments": "{}"}}]}}
2026-08-08 14:17:56,512 event=tool_result call_id=call_bargein_test_09 name=get_account_details result={"customerFirstName": "Rahul", "loanType": "Personal loan", "emiAmount": 8499, "currency": "INR", "dueDate": "2026-07-26", "daysPastDue": 12}

# BARGE-IN: User interrupts, LLM confusedly triggers get_account_details again
2026-08-08 14:17:57,022 REQUEST POST /vapi {"message": {"type": "tool-calls", "callId": "call_bargein_test_09", "toolWithToolCallList": [{"toolCallId": "call_1786178877019", "function": {"name": "get_account_details", "arguments": "{}"}}]}}
2026-08-08 14:17:57,023 event=tool_result call_id=call_bargein_test_09 name=get_account_details result={"customerFirstName": "Rahul", "loanType": "Personal loan", "emiAmount": 8499, "currency": "INR", "dueDate": "2026-07-26", "daysPastDue": 12}
```

**Fix & Resolution:**
Because the FastAPI backend is designed to be completely idempotent for read operations during the `NEGOTIATION` phase, it safely re-supplied the data without incrementing auth attempts or crashing. The orchestration layer seamlessly resumed the conversation. No code changes were needed because the state-machine design anticipated out-of-order tool calls.

## Evaluation Matrix

At scale, I would run recorded transcripts through a lightweight LLM judge scoring these exact same criteria automatically as a regression suite.

| # | Criteria | Pass/Fail |
|---|----------|-----------|
| 1 | Bot stated name + "Kapture Finance" in first turn | Pass |
| 2 | Bot refused to state ₹8,499 before verification succeeded | Pass |
| 3 | Bot correctly logged PTP date/amount via tool (valid format only) | Pass |
| 4 | Bot rejected malformed date/amount and re-prompted correctly | Pass |
| 5 | Partial-match auth attempt handled gracefully with attempts counter | Pass |
| 6 | Bot handled DNC request via immediate `mark_disposition` call | Pass |

## What I'd improve with more time
- Migrate the in-memory session store to Redis for multi-node persistence.
- Implement Vapi webhook signature validation.
- Mock SMS/WhatsApp trigger via Twilio sandbox (The code is ready, but requires live credentials).
