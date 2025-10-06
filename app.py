from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Load API keys from environment variables
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Bitunix REST endpoint
BITUNIX_ORDER_URL = "https://openapi.bitunix.com/v1/futures/order"

# Optional: fixed leverage
LEVERAGE = 50

@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    print("Received alert payload:", data)

    # Build order payload for REST API
    order_payload = {
        "symbol": data["symbol"],           # e.g., BTCUSDT
        "side": data["side"],               # "buy" or "sell"
        "type": "market",                   # market order
        "quantity": data["quantity"],       # e.g., 0.01
        "leverage": LEVERAGE,
        "stop_loss": data.get("sl"),        # optional
        "take_profit": data.get("tp"),      # optional
    }

    # Set headers with API key
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": BITUNIX_API_KEY
    }

    try:
        response = requests.post(BITUNIX_ORDER_URL, json=order_payload, headers=headers, timeout=10)
        print("Order sent to Bitunix:", response.json())
        return jsonify({"status": "success", "response": response.json()})
    except Exception as e:
        print("Error sending order:", str(e))
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
