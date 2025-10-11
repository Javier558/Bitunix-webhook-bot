from flask import Flask, request, jsonify
import requests
import os
import time
import json
import hashlib
import uuid

app = Flask(__name__)

# Environment Variables
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

BASE_URL = "https://fapi.bitunix.com"  # Bitunix base URL
LEVERAGE = 50
RETRY_DELAY = 0.5
MAX_RETRIES = 5

# ------------------------------------------------------------
# ✅ Refined Bitunix Signature Function
# ------------------------------------------------------------
def generate_signature(api_key, secret_key, query_params=None, body=None):
    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() * 1000))

    # Handle query params, sorting them by key
    sorted_query = ''
    if query_params:
        sorted_params = sorted(query_params.items())
        sorted_query = ''.join(f"{k}{v}" for k, v in sorted_params)

    # Handle body (JSON)
    body_str = ''
    if body:
        body_str = json.dumps(body, separators=(',', ':'), sort_keys=True)

    # Digest and sign
    digest_input = (nonce + timestamp + api_key + sorted_query + body_str).encode('utf-8')
    digest = hashlib.sha256(digest_input).hexdigest()
    sign_input = (digest + secret_key).encode('utf-8')
    sign = hashlib.sha256(sign_input).hexdigest()

    headers = {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json"
    }
    return headers

# ------------------------------------------------------------
# ✅ Request Helper with Retries
# ------------------------------------------------------------
def send_request(method, endpoint, body=None, query=None):
    url = f"{BASE_URL}{endpoint}"
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("WARNING: API keys are not set.")
        return None

    headers = generate_signature(BITUNIX_API_KEY, BITUNIX_API_SECRET, query, body)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "POST":
                r = requests.post(url, headers=headers, json=body, timeout=10)
            elif method.upper() == "DELETE":
                r = requests.delete(url, headers=headers, json=body, timeout=10)
            else:
                r = requests.get(url, headers=headers, params=query, timeout=10)

            r.raise_for_status()
            resp_json = r.json()
            if resp_json.get("code") == 0:
                return resp_json
            else:
                print(f"Bitunix API error: {resp_json.get('msg')}")
                time.sleep(RETRY_DELAY)
        except requests.exceptions.RequestException as e:
            print(f"Request error attempt {attempt}: {e}")
            time.sleep(RETRY_DELAY)
        except json.JSONDecodeError:
            print(f"Error decoding JSON response: {r.text}")
            time.sleep(RETRY_DELAY)
    return None

# ------------------------------------------------------------
# ✅ Get Open Positions
# ------------------------------------------------------------
def get_open_positions(symbol):
    resp = send_request("GET", "/api/v1/futures/trade/positions", query={"symbol": symbol})
    if resp and "data" in resp:
        return [p for p in resp["data"] if float(p.get("positionAmt", 0)) != 0]
    return []

# ------------------------------------------------------------
# ✅ Close all positions for a symbol
# ------------------------------------------------------------
def close_all_positions(symbol):
    positions = get_open_positions(symbol)
    if not positions:
        return {"code": 1, "msg": "No open positions to close"}
    endpoint = "/api/v1/futures/trade/close_all_position"
    body = {"symbol": symbol}
    resp = send_request("POST", endpoint, body=body)
    if resp:
        return resp
    return {"code": 1, "msg": "Failed to close all positions"}

# ------------------------------------------------------------
# ✅ Place limit order with SL/TP and guaranteed SL
# ------------------------------------------------------------
def place_limit_order(symbol, side, quantity, sl=None, tp=None, guaranteed_sl=False):
    ticker_resp = send_request("GET", "/api/v1/futures/market/depth", query={"symbol": symbol, "limit": 1})
    if not ticker_resp or "data" not in ticker_resp:
        print("❌ Failed to fetch order book data.")
        return None

    data = ticker_resp["data"]
    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        print("❌ Empty bids or asks")
        return None

    last_price = (float(bids[0][0]) + float(asks[0][0])) / 2

    order_body = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "price": last_price,
        "quantity": quantity,
        "timeInForce": "GTC",
        "leverage": LEVERAGE,
        "stopLossPrice": sl,
        "takeProfitPrice": tp,
        "guaranteedStopLoss": guaranteed_sl
    }
    # Remove None values
    order_body = {k: v for k, v in order_body.items() if v is not None}

    return send_request("POST", "/api/v1/futures/trade/place_order", body=order_body)

# ------------------------------------------------------------
# ✅ Webhook route
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Expected JSON payload"}), 415

        symbol = data.get("symbol")
        side = data.get("side")
        quantity = data.get("quantity")
        sl = data.get("sl")
        tp = data.get("tp")
        guaranteed_sl = data.get("guaranteed_stop_loss", False)

        if not symbol or not side or quantity is None:
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        if float(quantity) == 0.0:
            resp = close_all_positions(symbol)
            if resp.get("code") == 0:
                return jsonify({"status": "success", "response": resp}), 200
            else:
                return jsonify({"status": "failed", "message": resp.get("msg")}), 500

        # Place limit order
        resp = place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl)
        if resp:
            return jsonify({"status": "success", "response": resp}), 200
        else:
            return jsonify({"status": "failed", "message": "Failed to place limit order"}), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"status": "failed", "message": "Internal Server Error"}), 500

# ------------------------------------------------------------
# ✅ Run server
# ------------------------------------------------------------
if __name__ == "__main__":
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("WARNING: BITUNIX_API_KEY or BITUNIX_API_SECRET environment variables are not set.")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
