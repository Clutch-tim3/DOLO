import requests
import json
import time

BASE_URL = "http://localhost:5000"

def test_subscriptions():
    print("--- Testing Starter Tier ---")
    headers = {"X-Company-ID": "starter_corp"}
    
    # Check status
    res = requests.get(f"{BASE_URL}/api/subscription-status", headers=headers)
    print("Status:", res.json()["tier"])
    
    # Try prediction with Conquest (Should fail for Starter)
    res = requests.post(f"{BASE_URL}/api/predict", headers=headers, data={"model_version": "conquest"})
    print("Predict with Conquest (Expected 403):", res.status_code, res.text)
    
    # Try Agent Chat
    res = requests.post(f"{BASE_URL}/api/agent/chat", headers=headers, json={"message": "hello"})
    print("Agent Chat (Expected 403):", res.status_code, res.text)
    
    
    print("\n--- Testing Pro Tier ---")
    headers = {"X-Company-ID": "pro_corp"}
    
    # Check status
    res = requests.get(f"{BASE_URL}/api/subscription-status", headers=headers)
    print("Status:", res.json()["tier"])
    
    # Try prediction with Conquest (Should succeed for Pro, might fail with 400 due to no file but not 403)
    res = requests.post(f"{BASE_URL}/api/predict", headers=headers, data={"model_version": "conquest"})
    print("Predict with Conquest (Expected not 403):", res.status_code)
    
    # Try Agent Chat standard
    res = requests.post(f"{BASE_URL}/api/agent/chat", headers=headers, json={"message": "hello"})
    print("Agent Chat Standard (Expected 200):", res.status_code, res.json())
    
    # Exhaust Quote Quota
    print("Generating quotes to hit limit...")
    for i in range(6):
        res = requests.post(f"{BASE_URL}/api/agent/chat", headers=headers, json={
            "message": "quote", 
            "action": "generate_quote",
            "tender_file_path": "alfred_duma.txt"
        })
        if res.status_code == 402:
            print(f"Call {i+1} Blocked (Expected):", res.json())
        else:
            print(f"Call {i+1} Succeeded")

if __name__ == "__main__":
    test_subscriptions()
