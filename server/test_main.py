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
                    "toolCall": {
                        "id": "call_abc123",
                        "function": {
                            "name": "get_account_details",
                            "arguments": "{}"
                        }
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
                    "toolCall": {
                        "id": "call_def456",
                        "function": {
                            "name": "verify_customer",
                            "arguments": '{"idLast4": "4821"}'
                        }
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
                    "toolCall": {
                        "id": "call_ghi789",
                        "function": {
                            "name": "get_account_details",
                            "arguments": "{}"
                        }
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

def test_current_vapi_function_payload_verifies_last_four_digits():
    """Vapi's current Function-tool payload uses function.parameters."""
    sessions.clear()
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "test_current_vapi_payload"},
            "toolWithToolCallList": [
                {
                    "toolCall": {
                        "id": "call_current_vapi",
                        "type": "function",
                        "function": {
                            "name": "verify_customer",
                            "parameters": {"idLast4": "4821"}
                        }
                    }
                }
            ],
            "toolCallList": [
                {
                    "id": "call_current_vapi",
                    "name": "verify_customer",
                    "arguments": {"idLast4": "4821"}
                }
            ]
        }
    }

    response = client.post("/vapi", json=payload)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["toolCallId"] == "call_current_vapi"
    assert isinstance(result["result"], str)
    assert json.loads(result["result"])["verified"] is True


def test_verification_rejects_id_in_an_unrelated_argument():
    sessions.clear()
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "test_wrong_argument"},
            "toolCallList": [
                {
                    "id": "call_wrong_argument",
                    "name": "verify_customer",
                    "arguments": {"notes": "4821"}
                }
            ]
        }
    }

    response = client.post("/vapi", json=payload)
    result = json.loads(response.json()["results"][0]["result"])
    assert result["verified"] is False
