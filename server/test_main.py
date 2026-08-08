import json
from fastapi.testclient import TestClient
from main import app, sessions

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_auth_gate_rejects_unverified_account_details():
    # Clear sessions for test isolation
    sessions.clear()
    
    # 1. Simulate a call that has NOT been verified
    # The session will be created automatically in get_session
    call_id = "test_call_001"
    
    # 2. Try to call get_account_details (which requires auth)
    payload = {
        "message": {
            "type": "tool-calls",
            "callId": call_id,
            "toolWithToolCallList": [
                {
                    "toolCallId": "call_abc123",
                    "function": {
                        "name": "get_account_details",
                        "arguments": "{}"
                    }
                }
            ]
        }
    }
    
    response = client.post("/vapi", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert "results" in data
    assert len(data["results"]) == 1
    
    result_str = data["results"][0]["result"]
    result_obj = json.loads(result_str)
    
    # Assert the auth gate successfully blocked the request
    assert result_obj.get("error") == "auth_required"
    assert "until identity verification succeeds" in result_obj.get("message")

def test_auth_gate_allows_verified_account_details():
    sessions.clear()
    call_id = "test_call_002"
    
    # 1. Verify successfully
    verify_payload = {
        "message": {
            "type": "tool-calls",
            "callId": call_id,
            "toolWithToolCallList": [
                {
                    "toolCallId": "call_def456",
                    "function": {
                        "name": "verify_customer",
                        "arguments": '{"idLast4": "4821"}'
                    }
                }
            ]
        }
    }
    client.post("/vapi", json=verify_payload)
    
    # 2. Fetch account details
    details_payload = {
        "message": {
            "type": "tool-calls",
            "callId": call_id,
            "toolWithToolCallList": [
                {
                    "toolCallId": "call_ghi789",
                    "function": {
                        "name": "get_account_details",
                        "arguments": "{}"
                    }
                }
            ]
        }
    }
    response = client.post("/vapi", json=details_payload)
    data = response.json()
    result_str = data["results"][0]["result"]
    result_obj = json.loads(result_str)
    
    # Assert we got the actual data since we verified first
    assert "error" not in result_obj
    assert result_obj.get("emiAmount") == 8499
