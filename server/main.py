import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request, BackgroundTasks, Response
from pydantic import BaseModel
from twilio.rest import Client

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
    "dateOfBirth": "2005-11-22",
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
        "message": f"Operation '{operation}' is unavailable until identity verification succeeds. Ask the user for their DOB and last 4 digits of ID before proceeding."
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
            
            provided_dob = str(params.get("dateOfBirth", "")).lower()
            dob_matches = ("2005" in provided_dob) and (("11" in provided_dob) or ("nov" in provided_dob)) and ("22" in provided_dob)
            if params.get("dateOfBirth") == MOCK_CUSTOMER["dateOfBirth"]:
                dob_matches = True
                
            # Treat ID digits as string for comparison
            id_matches = str(params.get("idLast4", "")) == MOCK_CUSTOMER["idLast4"]
            
            session["auth_attempts"] += 1
            is_valid = dob_matches and id_matches
            
            append_event(session, "verify_customer", {
                "attempt": session["auth_attempts"],
                "result": "verified" if is_valid else "failed"
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
    results = []
    
    for item in tool_calls:
        # Handle different Vapi payload shapes
        func_obj = item.get("function") or item.get("toolCall", item)
        name = func_obj.get("name") or item.get("name")
        call_id_val = item.get("toolCallId") or item.get("id")
        
        raw_params = func_obj.get("arguments") or item.get("parameters", {})
        if isinstance(raw_params, str):
            try:
                params = json.loads(raw_params)
            except json.JSONDecodeError:
                params = {}
        else:
            params = raw_params
            
        result = execute_tool(session, name, params)
        logger.info(f"event=tool_result call_id={call_id} name={name} result={json.dumps(result)}")
        
        results.append({
            "name": name,
            "toolCallId": call_id_val,
            "result": json.dumps(result)
        })
        
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
