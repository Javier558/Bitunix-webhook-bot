from flask import Flask, request, jsonify
import json
import time
import websocket
import hmac
import hashlib
import base64
import threading

app = Flask(__name__)

# === BITUNIX API KEYS ===
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

# === Helper: create authentication payload ===
def generate_auth_payload(api_key, api_secret):
    ts = str(int(time.time() * 1000))
    signature_payload = ts + "GET" + "/ws-api/v1/private"
    signature = hmac.new(api_secret.encode(), signature_payload.encode(), hashlib.sha256).hexdigest()
    auth_data = {
        "op": "login",
        "args": [api_key, ts, signature]
    }
    return auth_data

# === Function to send order to Bitunix WebSocket ===
def send_order(order):
    def run_ws():
        ws = websocket.WebSocket()
        ws.connect("wss://fapi.bitunix.com/private/")

        # Authenticate
        auth = generate_auth_payload(API_KEY, API_SECRET)
        ws.send(json.dumps(auth))
        resp = ws.recv()
        print("Auth response:", resp)

        # Send order
        ws.send(json.dumps(order))
        result = ws.recv()
        print("Order result:", result)

        ws.close()

    thread = threading.Thread(target=run_ws)
    thread.start()

# === Flask route for TradingView webhook ===
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()
    print("Received alert payload:", data)

    # Prepare Bitunix order payload
    order = {
        "symbol": data["symbol"],
        "side": data["side"],
        "type": "market",
        "quantity": data["quantity"],
        "leverage": 50,
        "stop_loss": data.get("sl"),
        "take_profit": data.get("tp")
    }

    # Send order to Bitunix
    send_order(order)

    return jsonify({"status": "order sent", "payload": order})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
