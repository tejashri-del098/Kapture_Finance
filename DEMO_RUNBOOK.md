# Live Vapi Setup and Recording Runbook

Use this after signing in to Vapi. The local implementation is ready; no code edits are required.

## 1. Start and expose the webhook

In the project folder:

```bash
npm start
ngrok http 3000
```

Copy the `https://...` forwarding address from ngrok and confirm:

```bash
curl https://YOUR-TUNNEL-URL/health
```

Expected response: `{"status":"ok","activeSessions":0}`.

## 2. Generate the Vapi import payload

```bash
npm run build:vapi
```

Open `vapi-assistant-payload.json`, replace `https://YOUR-PUBLIC-HTTPS-URL/vapi` with the ngrok URL plus `/vapi`, then import/create the assistant in Vapi. If configuring in the dashboard instead of importing:

1. Copy `system-prompt.md` into the assistant system prompt.
2. Add the nine function schemas from `tool-schemas.json`.
3. Set the Assistant Server URL to `https://YOUR-TUNNEL-URL/vapi`.
4. Enable call recording and interruption/barge-in.
5. Attach a Vapi test phone number.

## 3. Test data

| Item | Demo value |
|---|---|
| Customer | Rahul Sharma |
| DOB | 1990-05-14 |
| Last four digits | 4821 |
| EMI | ₹8,499 |

## 4. Record the demo (target: 3 minutes)

### Call 1: successful promise-to-pay (about 90 seconds)

1. Answer as Rahul Sharma.
2. Supply the test DOB and last four digits.
3. Ask the bot for the amount after successful verification.
4. Say: “I will pay ₹8,499 on 2026-09-01. Please send the link by SMS.”
5. Confirm that a promise-to-pay and payment-link result are logged.

### Call 2: already-paid edge case (about 45 seconds)

1. Authenticate again.
2. Say: “I paid this on the 3rd.”
3. Show respectful acknowledgement, escalation, and the `already_paid_claimed` disposition.

### Security proof (about 20 seconds)

1. Start a third call without authenticating.
2. Ask: “How much do I owe?”
3. Show that the bot refuses to discuss confidential account information.

### Final screen share (about 25 seconds)

Show the Vapi recording/call detail, the webhook terminal logs, `README.md`, and the HLD PDF. Copy the recording or Loom URL into the submission email.

## 5. Before sending

- Verify that the recording plays without asking for access.
- Verify that no real customer details or credentials were used.
- Use the corrected HLD PDF, not the original HLD.
- Include the demo link and Drive-folder link in `SUBMISSION_EMAIL.md`.
