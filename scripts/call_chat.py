import requests

url = "http://127.0.0.1:8000/api/chat"
payload = {
    "message": "Tell me about this repo",
    "repo_url": "https://github.com/microsoft/vscode",
}
try:
    r = requests.post(url, json=payload, timeout=30)
    print(r.status_code)
    print(r.text)
except Exception as e:
    print("EXCEPTION:", e)
