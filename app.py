from flask import Flask, request, jsonify
import requests
import os
import time
import json
import hashlib
import uuid

app = Flask(__name__)

# Environment Variables for Security
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

BASE_URL = "https://api.bitunix.com"  # Main endpoint

# ------------------------------------------------------------
# ✅ Correct Bitunix Signature Function
# ------------------------------------------------------------
def generate_signature(api_key, secret_key, query_params=None, body=None):
    """
    Returns headers including api-key, nonce, timestamp, and sign.
    Matches Bitunix official documentation exactly.
    """
    nonce = str(uuid.uuid4()).replace("-", "")[:32]  # 32-char random string
    timestamp = str(int(time.time() * 1000))  # milliseconds

    # Convert query params to sorted ASCII string
    if query_params:
        sorted_query = ''.join(f"{k}{v}" for k, v in sorted(query_params.items()))
    else:
        sorted_query = ''

    # Convert body to compact JSON string (no spaces)
    if body:
        body_str = json.dumps(body, separators=(',', ':'))
    else:
        body_str = ''

    # Step 1: digest
    digest_input = nonce + timestamp + api_key + sorted_query + body_str
    digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()

    # Step 2: final sign
    sign_input = digest + secret_key
    sign = hashlib.sha256(sign_input.encode('utf-8')).hexdigest()

    headers = {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json"
    }
    return headers


# ------------------------------------------------------------
# ✅ Helper Functions
# ------------------------------------------------------------
def send_request(method, endpoint, body=None, query=None, retries=5):
    url = f"{BASE_URL}{endpoint}"
    headers = generate_signature(BITUNIX_API_KEY, BITUNIX_API_SECRET, query, body)
    print("Sending request:", method, url)
    print("Headers being sent:", headers)
    print("Body being sent:", body)

    for attempt in range(1, retries + 1):
        try:
            if method == "POST":
                r = requests.post(url, headers=headers, json=body, timeout=10)
            elif method == "DELETE":
                r = requests.delete(url, headers=headers, json=body, timeout=10)
            else:
                r = requests.get(url, headers=headers, params=query, timeout=10)

            response_json = r.json()
            print(f"Attempt {attempt}: {response_json}")

            # If successful response
            if response_json.get("code") == 0:
                return response_json
            else:
                time.sleep(1)

        except Exception as e:
            print(f"Error on attempt {attempt}: {e}")
            time.sleep(1)
    return None


# ------------------------------------------------------------
# ✅ Core Trading Functions
# ------------------------------------------------------------
def close_all_positions(symbol):
    print("Closing all open positions...")
    endpoint = "/v1/private/position/close-all"
    body = {"symbol": symbol}
    return send_request("POST", endpoint, body=body)


def place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl=False):
    """
    Places a limit order using the last price, sets SL/TP, supports guaranteed SL.
    """
    # Get current price
    ticker_resp = send_request("GET", f"/v1/public/ticker", query={"symbol": symbol})
    if not ticker_resp or "data" not in ticker_resp:
        print("❌ Failed to fetch ticker data.")
        return None

    last_price = float(ticker_resp["data"].get("lastPrice", 0))
    print(f"✅ Last price for {symbol}: {last_price}")

    # Prepare order body
    order_body = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "price": last_price,
        "quantity": quantity,
        "timeInForce": "GTC",
        "stopLossPrice": sl,
        "takeProfitPrice": tp,
        "guaranteedStopLoss": guaranteed_sl
    }

    endpoint = "/v1/private/order/create"
    return send_request("POST", endpoint, body=order_body)


def partial_position_close(symbol, quantity):
    """
    Closes part of an open position.
    """
    print(f"Partial position closing for {symbol}, qty: {quantity}")
    endpoint = "/v1/private/position/close-partial"
    body = {"symbol": symbol, "quantity": quantity}
    return send_request("POST", endpoint, body=body)


# ------------------------------------------------------------
# ✅ Flask Route (Main Entry)
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    print("Received alert payload:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    quantity = data.get("quantity")
    sl = data.get("sl")
    tp = data.get("tp")
    guaranteed_sl = data.get("guaranteed_stop_loss", False)

    # Close all open positions first
    close_all_positions(symbol)

    # Place new limit order
    resp = place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl)

    if resp:
        return jsonify({"status": "success", "response": resp})
    else:
        return jsonify({"status": "failed"}), 500


# ------------------------------------------------------------
# ✅ Run Server
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
