import os
import json
import logging
from hmac import compare_digest
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from twilio.rest import Client

def mask_pii(data: Any) -> Any:
    """Recursively mask sensitive PII fields in dictionaries and lists."""
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            if k in ["idLast4", "phone", "accountId", "customerFirstName", "firstName", "number"]:
                masked[k] = "****"
            else:
                masked[k] = mask_pii(v)
        return masked
    elif isinstance(data, list):
        return [mask_pii(item) for item in data]
    return data

# -----------------------------------------------------------------------------
# 1. Logging Configuration (Persistent to server.log)
# -----------------------------------------------------------------------------
logging.basicConfig(
    filename="server.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# 2. Domain & Mock Data
# -----------------------------------------------------------------------------
class Phase:
    GREETING = "greeting"
    AUTH = "auth"
    VERIFIED = "verified"
    NEGOTIATION = "negotiation"
    CLOSING = "closing"
    ENDED = "ended"

class Disposition:
    PTP_CAPTURED = "ptp_captured"
    ALREADY_PAID_CLAIMED = "already_paid_claimed"
    DISPUTE_LOGGED = "dispute_logged"
    HARDSHIP_ESCALATED = "hardship_escalated"
    DNC_OPT_OUT = "dnc_opt_out"
    WRONG_NUMBER = "wrong_number"
    NO_DISCLOSURE_UNVERIFIED = "no_disclosure_unverified"
    NO_RESPONSE = "no_response"
    VOICEMAIL_LEFT = "voicemail_left"
    TECHNICAL_FAILURE = "technical_failure"

MOCK_CUSTOMER = {
    "phone": "+919302174610",
    "firstName": "Rahul",
    "idLast4": "4821",
    "accountId": "loan_rahul_001",
    "loanType": "Personal loan",
    "emiAmount": 8499,
    "currency": "INR",
    "dueDate": "2026-07-26",
    "daysPastDue": 12
}

# In-memory session store
# Keyed by call_id
sessions: Dict[str, Dict[str, Any]] = {}

def create_session(call_id: str, phone: Optional[str]) -> dict:
    return {
        "call_id": call_id,
        "phone": phone,
        "phase": Phase.GREETING,
        "auth_attempts": 0,
        "verified": False,
        "account_id": None,
        "dnc_requested": False,
        "ptp": None,
        "disposition": None,
        "events": []
    }

def get_session(call_id: str, phone: Optional[str] = None) -> dict:
    if call_id not in sessions:
        sessions[call_id] = create_session(call_id, phone)
        logger.info(f"event=session_created call_id={call_id} phone={phone}")
    return sessions[call_id]

def append_event(session: dict, event_type: str, details: dict = None):
    if details is None:
        details = {}
    session["events"].append({"type": event_type, "at": datetime.utcnow().isoformat(), **details})

def is_future_date(date_str: str) -> bool:
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
        return parsed > datetime.now()
    except ValueError:
        return False

# -----------------------------------------------------------------------------
# 3. FastAPI App & Middleware
# -----------------------------------------------------------------------------
app = FastAPI()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log incoming request body (if any) safely
    body = b""
    if request.method in ["POST", "PUT", "PATCH"]:
        body = await request.body()
    
    # We must reset the body stream so downstream can read it
    async def receive():
        return {"type": "http.request", "body": body}
    request._receive = receive
    
    body_str = body.decode("utf-8") if body else ""
    try:
        if body_str:
            body_json = json.loads(body_str)
            body_str = json.dumps(mask_pii(body_json))
    except Exception:
        pass
    logger.info(f"REQUEST {request.method} {request.url.path} {body_str}")
    
    response = await call_next(request)
    
    # We can't easily read the response body in middleware without consuming it,
    # so we just log the status code. Tool results will be logged individually.
    logger.info(f"RESPONSE {request.method} {request.url.path} {response.status_code}")
    
    return response

# -----------------------------------------------------------------------------
# 4. Auth Gate & Tools
# -----------------------------------------------------------------------------
def require_verified(session: dict, operation: str) -> Optional[dict]:
    """
    STRICT AUTH GATE: Returns an error response if the session is not verified.
    Returns None if verified.
    """
    if session.get("verified") and session.get("account_id"):
        return None
    
    append_event(session, "policy_blocked", {"operation": operation})
    return {
        "error": "auth_required",
        "message": f"Operation '{operation}' is unavailable until identity verification succeeds. Ask the user for the last 4 digits of their ID before proceeding."
    }

def close_call(session: dict, code: str, notes: str = "") -> dict:
    session["disposition"] = code
    session["phase"] = Phase.CLOSING
    append_event(session, "disposition", {"code": code, "notes": notes})
    session["phase"] = Phase.ENDED
    return {"status": "ack", "dispositionCode": code}

def send_sms(phone: str, amount: float) -> dict:
    """
    Checks for Twilio credentials in environment. 
    If absent, simulates the response honestly.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    
    if not (account_sid and auth_token and from_number):
        logger.info(f"Simulating Twilio SMS to {phone} for {amount} INR (No credentials found)")
        return {
            "status": "delivered",
            "simulated": True,
            "sid": f"SM_mock_{datetime.now().timestamp()}",
            "to": phone,
            "timestamp": datetime.now().isoformat()
        }
    
    try:
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=f"Kapture Finance: Your overdue EMI is {amount} INR. Tap here to pay via secure link.", 
            from_=from_number, 
            to=phone
        )
        logger.info(f"Real Twilio SMS sent! SID: {message.sid}")
        return {
            "status": "queued",
            "simulated": False,
            "sid": message.sid,
            "to": phone,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to send real Twilio SMS: {e}")
        # Fall back to simulated so the bot doesn't completely crash if Twilio is misconfigured
        return {
            "status": "failed",
            "simulated": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

# Tool execution dispatcher
def execute_tool(session: dict, name: str, params: dict) -> dict:
    try:
        if name == "verify_customer":
            if session["phase"] == Phase.ENDED:
                return {"status": "POLICY_BLOCKED", "reason": "The call is already closed."}
            session["phase"] = Phase.AUTH
            
            # Only the idLast4 argument is an authentication factor.  Do not
            # search the whole parameters object: a different argument must
            # never be able to make authentication succeed accidentally.
            provided_id = "".join(str(params.get("idLast4", "")).split())
            id_matches = (
                len(provided_id) == 4
                and provided_id.isdigit()
                and compare_digest(provided_id, MOCK_CUSTOMER["idLast4"])
            )
            
            session["auth_attempts"] += 1
            is_valid = id_matches
            
            append_event(session, "verify_customer", {
                "attempt": session["auth_attempts"],
                "result": "verified" if is_valid else "failed",
                "params": params
            })
            
            if is_valid:
                session["verified"] = True
                session["account_id"] = MOCK_CUSTOMER["accountId"]
                session["phase"] = Phase.VERIFIED
                return {
                    "verified": True,
                    "attemptsLeft": max(0, 3 - session["auth_attempts"]),
                    "accountId": session["account_id"]
                }
            
            attempts_left = max(0, 3 - session["auth_attempts"])
            if attempts_left == 0:
                close_call(session, Disposition.NO_DISCLOSURE_UNVERIFIED, "Three failed verification attempts")
            return {"verified": False, "attemptsLeft": attempts_left, "endCall": attempts_left == 0}
            
        elif name == "get_account_details":
            blocked = require_verified(session, "get_account_details")
            if blocked: return blocked
            session["phase"] = Phase.NEGOTIATION
            append_event(session, "get_account_details", {"accountId": session["account_id"]})
            return {
                "customerFirstName": MOCK_CUSTOMER["firstName"],
                "loanType": MOCK_CUSTOMER["loanType"],
                "emiAmount": MOCK_CUSTOMER["emiAmount"],
                "currency": MOCK_CUSTOMER["currency"],
                "dueDate": MOCK_CUSTOMER["dueDate"],
                "daysPastDue": MOCK_CUSTOMER["daysPastDue"]
            }
            
        elif name == "log_promise_to_pay":
            blocked = require_verified(session, "log_promise_to_pay")
            if blocked: return blocked
            date_str = params.get("ptpDate", "")
            amount = params.get("ptpAmount")
            
            if not is_future_date(date_str):
                return {"error": "format_rejected", "message": "Date must be YYYY-MM-DD and in the future. Prompt the user for exact date."}
            
            try:
                amount = float(amount)
                if amount <= 0: raise ValueError
            except (ValueError, TypeError):
                return {"error": "format_rejected", "message": "Amount must be a positive number."}
                
            session["ptp"] = {"date": date_str, "amount": amount}
            append_event(session, "promise_to_pay", session["ptp"])
            return {"status": "logged", "confirmationId": f"ptp_{session['call_id']}"}
            
        elif name == "send_payment_link":
            blocked = require_verified(session, "send_payment_link")
            if blocked: return blocked
            if not session.get("ptp"):
                return {"error": "policy_blocked", "message": "A payment link may be sent only after a promise-to-pay is logged."}
            channel = params.get("channel", "sms")
            if channel not in ["sms", "whatsapp"]:
                return {"error": "validation_error", "message": "Use sms or whatsapp."}
                
            append_event(session, "payment_link_sent", {"channel": channel, "amount": session["ptp"]["amount"]})
            sms_response = send_sms(session.get("phone") or MOCK_CUSTOMER["phone"], session["ptp"]["amount"])
            
            return {
                "status": "sent",
                "linkId": f"pay_{session['call_id']}",
                "channel": channel,
                "twilio_response": sms_response
            }
            
        elif name == "log_dispute":
            blocked = require_verified(session, "log_dispute")
            if blocked: return blocked
            append_event(session, "dispute", {"reason": params.get("reason", "other")})
            return {"status": "logged", "ticketId": f"dispute_{session['call_id']}"}
            
        elif name == "escalate_to_agent":
            blocked = require_verified(session, "escalate_to_agent")
            if blocked: return blocked
            append_event(session, "escalation", {"reason": params.get("reason", "customer_request")})
            return {"status": "queued", "ticketId": f"esc_{session['call_id']}", "queue": "collections-resolution"}
            
        elif name == "mark_disposition":
            code = params.get("dispositionCode")
            # We don't block this behind auth since we might dispose unverified calls (DNC, Wrong Number)
            return close_call(session, code, params.get("notes", ""))
            
        elif name == "record_do_not_call":
            session["dnc_requested"] = True
            append_event(session, "dnc_requested")
            return close_call(session, Disposition.DNC_OPT_OUT, "Customer requested no further calls")
            
        elif name == "record_wrong_person":
            append_event(session, "wrong_person")
            return close_call(session, Disposition.WRONG_NUMBER, "Caller says they are not the customer")
            
        else:
            return {"error": "not_found", "message": f"Unknown function: {name}"}
            
    except Exception as e:
        logger.error(f"Error in tool {name}: {str(e)}", exc_info=True)
        append_event(session, "tool_error", {"name": name, "message": str(e)})
        return {"error": "technical_failure", "message": "The requested operation could not be completed."}

# -----------------------------------------------------------------------------
# 5. Endpoints
# -----------------------------------------------------------------------------
def parse_tool_parameters(raw_params: Any) -> dict:
    """Return a parameters object from the Vapi/OpenAI tool-call variants."""
    if isinstance(raw_params, str):
        try:
            raw_params = json.loads(raw_params)
        except json.JSONDecodeError:
            return {}
    return raw_params if isinstance(raw_params, dict) else {}


def extract_vapi_tool_call(item: Any) -> tuple[Optional[str], Optional[str], dict]:
    """Normalize legacy and current Vapi tool-call payloads.

    Current Vapi function tools use ``toolCall.function.parameters``. Older
    assistant ``model.functions`` payloads may instead use
    ``toolCall.function.arguments`` or top-level ``toolCallList`` fields.
    """
    if not isinstance(item, dict):
        return None, None, {}

    tool_call = item.get("toolCall")
    if not isinstance(tool_call, dict):
        tool_call = {}
    function = tool_call.get("function") or item.get("function")
    if not isinstance(function, dict):
        function = {}

    name = function.get("name") or tool_call.get("name") or item.get("name")
    tool_call_id = item.get("toolCallId") or tool_call.get("id") or item.get("id")

    raw_params = (
        function.get("parameters")
        if "parameters" in function
        else function.get("arguments")
        if "arguments" in function
        else tool_call.get("parameters")
        if "parameters" in tool_call
        else tool_call.get("arguments")
        if "arguments" in tool_call
        else item.get("parameters", item.get("arguments", {}))
    )
    return name, tool_call_id, parse_tool_parameters(raw_params)


@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Kapture Finance | AI Voicebot Demo</title>
        <style>
            body {
                margin: 0;
                padding: 0;
                font-family: 'Inter', sans-serif;
                background: linear-gradient(135deg, #0f172a, #1e293b);
                color: #ffffff;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }
            .container {
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(15px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
                padding: 50px;
                text-align: center;
                max-width: 650px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            }
            h1 {
                font-size: 2.8rem;
                margin-bottom: 15px;
                background: -webkit-linear-gradient(#38bdf8, #818cf8);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-weight: 800;
            }
            p {
                color: #94a3b8;
                font-size: 1.15rem;
                line-height: 1.6;
                margin-bottom: 30px;
            }
            .instructions {
                margin-top: 30px;
                text-align: left;
                background: rgba(0,0,0,0.3);
                padding: 25px;
                border-radius: 12px;
                font-size: 1rem;
                color: #cbd5e1;
                border: 1px solid rgba(255,255,255,0.05);
            }
            .links {
                margin-top: 30px;
                display: flex;
                gap: 20px;
                justify-content: center;
            }
            a.btn {
                background: rgba(56, 189, 248, 0.1);
                color: #38bdf8;
                border: 1px solid #38bdf8;
                padding: 12px 24px;
                text-decoration: none;
                font-weight: 600;
                border-radius: 8px;
                transition: all 0.2s ease;
            }
            a.btn:hover {
                background: #38bdf8;
                color: #0f172a;
            }
        </style>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    </head>
    <body>
        <div class="container">
            <h1>Kapture Finance</h1>
            <p>Welcome to the <b>AI Delivery Intern</b> assignment demo. This is the secure backend orchestration server for <b>Maya</b>, our state-machine collections voicebot.</p>
            
            <p>If you are testing this project, please dial the phone number provided in the README, or use the Vapi share link provided by the candidate.</p>
            
            <div class="instructions">
                <strong style="color: #fff;">Test Credentials:</strong><br><br>
                • <b>Name:</b> Rahul Sharma<br>
                • <b>ID Number (Last 4):</b> 4821<br><br>
                <i>Security Note: Maya is strictly instructed to refuse to disclose debt amounts or send payment links until this ID is cryptographically verified by this backend server.</i>
            </div>
            
            <div class="links">
                <a href="/logs" class="btn">View Live Session Logs</a>
                <a href="https://github.com/tejashri-del098/Kapture_Finance" class="btn" target="_blank">View GitHub Repo</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/health")
def health_check():
    """Lightweight endpoint for UptimeRobot."""
    return {"status": "ok", "activeSessions": len(sessions)}

@app.post("/vapi")
async def vapi_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")
    
    msg = body.get("message", body)
    
    # Store in raw_logs (cap at 10 to avoid memory leak)
    raw_logs.append(mask_pii(body))
    if len(raw_logs) > 10:
        raw_logs.pop(0)
    
    # Extract Call ID and Phone
    call_info = msg.get("call", {})
    call_id = call_info.get("id") or msg.get("callId") or "unknown-call"
    phone = None
    if "customer" in call_info:
        phone = call_info["customer"].get("number")
    
    session = get_session(call_id, phone)
    
    msg_type = msg.get("type")
    if msg_type != "tool-calls":
        logger.info(f"event=vapi_event type={msg_type} call_id={call_id}")
        return {"received": True}
        
    tool_calls = msg.get("toolWithToolCallList", msg.get("toolCallList", []))
    
    logger.info(f"event=vapi_payload call_id={call_id} payload={json.dumps(mask_pii(msg))}")
    
    results = []
    
    for item in tool_calls:
        name, tool_call_id, params = extract_vapi_tool_call(item)
        if not name:
            result = {"error": "invalid_tool_call", "message": "Tool call is missing a function name."}
        else:
            result = execute_tool(session, name, params)
        logger.info(f"event=tool_result call_id={call_id} name={name} result={json.dumps(mask_pii(result))}")
        
        results.append({
            "name": name,
            "toolCallId": tool_call_id,
            # Vapi expects result to be a string even when the tool output is
            # structured JSON. This also gives the model a consistent format.
            "result": json.dumps(result, separators=(",", ":"))
        })
        
    return {"results": results}

raw_logs = []

@app.get("/rawlogs")
def get_raw_logs():
    return raw_logs

@app.get("/logs")
def get_logs():
    return mask_pii(sessions)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
